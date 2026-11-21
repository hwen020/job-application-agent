"""Batch material extraction and transactional review."""
import base64
import csv
from hashlib import sha256
import io
import json
import re
import zipfile
from pathlib import PurePath
from xml.etree import ElementTree

from .storage import audit, digest, normalized

KINDS = {'resume', 'experience', 'project', 'education', 'skill'}
SENSITIVE = re.compile(
    r'(?im)\b(?:password|passcode|otp|verification code|ssn|social security(?: number)?|'
    r'api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|session[_ -]?cookie|'
    r'reference(?: contact)? (?:phone|email))[\x22\x27]?[^\S\r\n]*[:=,|\t]|'
    r'(?:密码|验证码|推荐人电话|推荐人邮箱|社保号码)\s*[:：=，,]|'
    r'\b\d{3}-\d{2}-\d{4}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----|'
    r'https?://[^/\s]+:[^/\s]+@|\bbearer\s+[a-z0-9._-]+'
)


def clean(value, limit=4000):
    if not isinstance(value,str) or len(value)>limit or SENSITIVE.search(value):
        raise ValueError('Invalid or sensitive input')
    return value.strip()


def zip_xml(package, name):
    item = package.getinfo(name)
    if item.file_size > 8_000_000 or item.flag_bits & 1:
        raise ValueError('Unsupported document size or encryption')
    raw = package.read(item)
    if re.search(br'<!\s*(DOCTYPE|ENTITY)',raw,re.I):
        raise ValueError('Unsupported XML')
    return ElementTree.fromstring(raw)


def extract(raw, ext):
    if ext in ('.txt','.md','.markdown','.csv'):
        value = raw.decode('utf-8-sig')
        clean(value, 2_000_000)
        if ext == '.csv':
            return [' | '.join(row) for row in csv.reader(io.StringIO(value), strict=True)]
        return value.splitlines()
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('PDF support requires pypdf')
        try:
            reader = PdfReader(io.BytesIO(raw), strict=False)
            if reader.is_encrypted or len(reader.pages)>100:
                raise ValueError('Encrypted PDF or too many pages')
            return [line for page in reader.pages for line in (page.extract_text() or '').splitlines()]
        except Exception:
            raise ValueError('Cannot extract text from this PDF')
    if ext not in ('.xlsx','.docx'):
        raise ValueError('Unsupported file format')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            if ext == '.docx':
                root = zip_xml(package,'word/document.xml')
                return [''.join(node.text or '' for node in paragraph.findall('.//{*}t'))
                        for paragraph in root.findall('.//{*}p')]
            shared = []
            if 'xl/sharedStrings.xml' in package.namelist():
                shared = [''.join(node.text or '' for node in item.findall('.//{*}t'))
                          for item in zip_xml(package,'xl/sharedStrings.xml')]
            lines = []
            sheets = [name for name in package.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',name)]
            if len(sheets)>30:
                raise ValueError('Too many worksheets')
            for sheet in sorted(sheets):
                for row in zip_xml(package,sheet).findall('.//{*}row'):
                    values = []
                    for cell in row.findall('{*}c'):
                        value = cell.findtext('{*}v','')
                        cell_type = cell.get('t')
                        if cell_type == 's':
                            value = shared[int(value)]
                        elif cell_type == 'inlineStr':
                            value = ''.join(node.text or '' for node in cell.findall('.//{*}t'))
                        values.append(value)
                    lines.append(' | '.join(values))
            return lines
    except (zipfile.BadZipFile, KeyError, IndexError, ElementTree.ParseError):
        raise ValueError('Invalid Office document')


def plan(db, payload):
    files = payload.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=12:
        raise ValueError('Choose 1 to 12 files')
    output, seen_sources, seen_facts = [], set(), {}
    for item in files:
        name = clean(item['name'],240)
        kind = item['kind']
        subject = clean(item['subject'],240)
        if kind not in KINDS or not subject or not name or re.search(r'[/\\:]',name):
            raise ValueError('Invalid material name, category or subject')
        raw = base64.b64decode(item['content'],validate=True)
        if not raw or len(raw)>4_000_000:
            raise ValueError('File must be between 1 byte and 4 MB')
        key = sha256(raw).hexdigest()
        duplicate = key in seen_sources or db.execute('SELECT 1 FROM source_documents WHERE sha256=?',(key,)).fetchone() is not None
        seen_sources.add(key)
        entry = {'name':name,'subject':subject,'kind':kind,'sha256':key,'duplicate':duplicate,'rows':[]}
        output.append(entry)
        if duplicate:
            continue
        lines = extract(raw,PurePath(name).suffix.lower())
        if len(lines)>3000 or sum(map(len,lines))>500_000:
            raise ValueError('Too much extracted text')
        clean('\n'.join(lines), 510_000)
        if not any(line.strip() for line in lines):
            raise ValueError('No extractable text')
        for index, line in enumerate(lines,1):
            value = clean(line)
            if not value:
                continue
            # Structured records can use a stable label followed by a colon.
            label, sep, rest = value.partition(':')
            identity = digest(json.dumps([kind,normalized(subject),normalized(label if sep and rest.strip() else value)]))
            previous = seen_facts.get(identity)
            if previous is None:
                found = db.execute('SELECT statement FROM facts WHERE identity=?',(identity,)).fetchone()
                previous = found['statement'] if found else None
            state = 'new' if previous is None else ('duplicate' if normalized(previous)==normalized(value) else 'conflict')
            seen_facts.setdefault(identity,value)
            entry['rows'].append({'line':index,'statement':value,'identity':identity,'state':state,
                                  'previous':previous if state=='conflict' else None})
    return output


def token(entries):
    return digest(json.dumps(entries,ensure_ascii=False,sort_keys=True))


def commit(db,payload):
    db.execute('BEGIN IMMEDIATE')
    entries = plan(db,payload)
    if payload.get('preview') != token(entries):
        raise ValueError('Preview expired. Preview the batch again.')
    selections = payload.get('selections')
    if not isinstance(selections,dict):
        raise ValueError('Select reviewed rows')
    sources = facts = 0
    for entry in entries:
        selected = selections.get(entry['sha256'],[])
        if not isinstance(selected,list) or any(type(number) is not int for number in selected):
            raise ValueError('Invalid row selection')
        if entry['duplicate'] or not selected:
            continue
        rows = [row for row in entry['rows'] if row['line'] in selected]
        if len(rows)!=len(set(selected)) or any(row['state']=='conflict' for row in rows):
            raise ValueError('Conflicting or invalid rows must be removed from selection')
        source = db.execute('INSERT INTO source_documents(name,kind,sha256) VALUES(?,?,?)',
                            (entry['name'],entry['kind'],entry['sha256'])).lastrowid
        sources += 1
        if entry['kind']=='resume':
            series = normalized(entry['subject'])
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM resume_versions WHERE series=?',(series,)).fetchone()[0]
            db.execute('INSERT INTO resume_versions(series,version,source_id) VALUES(?,?,?)',(series,version,source))
        for row in rows:
            existing = db.execute('SELECT id FROM facts WHERE identity=?',(row['identity'],)).fetchone()
            if existing:
                fact = existing['id']
            else:
                fact = db.execute('INSERT INTO facts(identity,kind,subject,statement,source_id) VALUES(?,?,?,?,?)',
                                  (row['identity'],entry['kind'],entry['subject'],row['statement'],source)).lastrowid
                facts += 1
                table = {'project':'projects','education':'education','skill':'skills'}.get(entry['kind'])
                if table:
                    db.execute('INSERT INTO '+table+'(fact_id) VALUES(?)',(fact,))
            db.execute('INSERT INTO fact_sources VALUES(?,?,?)',(fact,source,str(row['line'])))
        audit(db,'import_confirmed','source',source)
    return {'sources':sources,'facts':facts}
