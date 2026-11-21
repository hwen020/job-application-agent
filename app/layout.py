"""Shared document layout and validation of emitted PDF pages."""
from dataclasses import dataclass
from io import BytesIO
import re


DEFAULT_TEMPLATE = {'margin':48, 'font_size':11, 'section_gap':13, 'bullet_gap':5, 'line_spacing':1.22}
BOUNDS = {'margin':(40,66),'font_size':(10,12),'section_gap':(8,20),'bullet_gap':(3,8),'line_spacing':(1.12,1.4)}
WIDTH, HEIGHT = 612, 792
INK = '#172536'


def settings(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT_TEMPLATE):
        raise ValueError('Unknown template setting')
    result = {**DEFAULT_TEMPLATE,**value}
    for key,(low,high) in BOUNDS.items():
        number = result[key]
        if type(number) not in (int,float) or not low<=number<=high:
            raise ValueError('Template setting outside supported range')
    return result


@dataclass(frozen=True)
class Paragraph:
    text: str
    kind: str = 'body'
    right: str = ''


def paragraph_layout(paragraph, template, scale, first=False):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    size = template['font_size']
    kind = paragraph.kind
    fonts = {'name':size+9,'section':size+1,'subject':size,'body':size,'bullet':size,'meta':size-0.5}
    points = fonts[kind]
    font = 'Helvetica-Bold' if kind in ('name','section','subject') else 'Helvetica'
    gap = {'name':0,'meta':5,'section':template['section_gap'],'subject':template['bullet_gap']+2,
           'body':template['bullet_gap']+3,'bullet':template['bullet_gap']}[kind]
    gap = 0 if first else max(2, gap*scale)
    indent = 15 if kind=='bullet' else 0
    capacity = WIDTH-2*template['margin']-indent-3
    right = paragraph.right
    if right:
        capacity -= stringWidth(right,font,points)+18
    lines, current = [], ''
    for word in paragraph.text.split():
        if stringWidth(word,font,points)>capacity:
            raise ValueError('A word is too wide for the document template')
        new = (current+' '+word).strip()
        if current and stringWidth(new,font,points)>capacity:
            lines.append(current)
            current = word
        else:
            current = new
    if current:
        lines.append(current)
    if not lines:
        raise ValueError('Empty document paragraph')
    return {'kind':kind,'font':font,'points':points,'gap':gap,'indent':indent,
            'lines':lines,'leading':points*template['line_spacing'],'right':right}


def page_layout(groups, template, scale):
    y = template['margin']
    result = []
    limit = HEIGHT-template['margin']-3
    for group in groups:
        for paragraph in group:
            layout = paragraph_layout(paragraph,template,scale,not result)
            layout['top'] = y+layout['gap']
            y = layout['top']+layout['leading']*len(layout['lines'])
            if y>limit:
                return None
            result.append(layout)
    return result,y


def paginate(groups, template):
    if not groups:
        raise ValueError('Document has no content')
    for scale in (1,0.70):
        single = page_layout(groups,template,scale)
        if single:
            for expanded in (1.65,1.45,1.25,1.10):
                if expanded<=scale or HEIGHT-template['margin']-single[1]<68:
                    break
                trial = page_layout(groups,template,expanded)
                if trial:
                    return [trial[0]],expanded
            return [single[0]],scale
    candidates = []
    for scale in (1.65,1.45,1.25,1,0.85,0.70):
        for index in range(2,len(groups)):
            first = page_layout(groups[:index],template,scale)
            second = page_layout(groups[index:],template,scale)
            if first and second:
                used = [part[1]-template['margin'] for part in (first,second)]
                score = abs(used[0]-used[1]) - 0.7*sum(used)
                candidates.append((score,[first[0],second[0]],scale))
    if candidates:
        _,pages,scale = min(candidates,key=lambda item:item[0])
        return pages,scale
    raise ValueError('Content will not fit in two pages at the minimum spacing; remove or shorten bullets')


def pdf_bytes(pages, template, title):
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen.canvas import Canvas
    output = BytesIO()
    canvas = Canvas(output,pagesize=(WIDTH,HEIGHT),invariant=True)
    canvas.setTitle(title)
    for page in pages:
        canvas.setFillColor(HexColor(INK))
        for item in page:
            margin = template['margin']
            point = item['points']
            canvas.setFont(item['font'],point)
            for index,line in enumerate(item['lines']):
                baseline = HEIGHT-item['top']-point-index*item['leading']
                canvas.drawString(margin+item['indent'],baseline,line)
                if index==0 and item['kind']=='bullet':
                    canvas.drawString(margin+3,baseline,'•')
                if index==0 and item['right']:
                    canvas.drawRightString(WIDTH-margin,baseline,item['right'])
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def docx_bytes(pages, template, title):
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_TAB_ALIGNMENT
    document = Document()
    document.core_properties.title = title
    section = document.sections[0]
    section.page_width,section.page_height = Pt(WIDTH),Pt(HEIGHT)
    section.top_margin = section.bottom_margin = Pt(template['margin'])
    section.left_margin = section.right_margin = Pt(template['margin'])
    normal = document.styles['Normal']
    normal.font.name = 'Arial'
    normal.font.color.rgb = RGBColor.from_string(INK[1:])
    for index,page in enumerate(pages):
        for pos,item in enumerate(page):
            p = document.add_paragraph(style='Heading 1' if item['kind']=='section' else 'Normal')
            form = p.paragraph_format
            if index and not pos:
                form.page_break_before = True
            form.space_before = Pt(item['gap'])
            form.space_after = Pt(0)
            form.line_spacing = Pt(item['leading'])
            form.keep_together = True
            form.keep_with_next = item['kind'] in ('section','subject','name')
            if item['kind']=='bullet':
                form.left_indent = Pt(item['indent'])
                form.first_line_indent = Pt(3-item['indent'])
                form.tab_stops.add_tab_stop(Pt(item['indent']))
            if item['right']:
                form.tab_stops.add_tab_stop(Pt(WIDTH-2*template['margin']),WD_TAB_ALIGNMENT.RIGHT)
            lines = list(item['lines'])
            if item['right']:
                lines[0] += '\t'+item['right']
            prefix = '•\t' if item['kind']=='bullet' else ''
            run = p.add_run(prefix+'\n'.join(lines))
            run.font.name = 'Arial'
            run.font.color.rgb = RGBColor.from_string(INK[1:])
            run.font.size = Pt(item['points'])
            run.bold = item['font'].endswith('Bold')
    destination = BytesIO()
    document.save(destination)
    return destination.getvalue()


def inspect_pdf(data,pages,template):
    import fitz
    from PIL import Image, ImageChops
    issues, warnings, stats = [],[],[]
    canonical = lambda value: re.sub(r'\s+',' ',value.replace('•','')).strip()
    with fitz.open(stream=data,filetype='pdf') as document:
        if document.page_count!=len(pages):
            issues.append('Unexpected rendered page count')
        for index,page in enumerate(document):
            expected = pages[index]
            rendered = page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),colorspace=fitz.csGRAY,alpha=False)
            image = Image.frombytes('L',(rendered.width,rendered.height),rendered.samples)
            bounds = ImageChops.invert(image).getbbox()
            if bounds is None:
                issues.append('Blank rendered page')
                continue
            raster_bounds = [round(v/1.5,2) for v in bounds]
            bottom = round(page.rect.height-raster_bounds[3],1)
            margin = template['margin']
            if (raster_bounds[0]<margin-2 or raster_bounds[1]<margin-2
                    or raster_bounds[2]>page.rect.width-margin+2 or bottom<margin-2):
                issues.append('Rendered ink exceeds template margins')
            required = []
            for item in expected:
                lines = list(item['lines'])
                if item['right']:
                    lines[0] += ' '+item['right']
                required.extend(lines)
            actual = canonical(page.get_text(sort=True))
            if not actual or canonical(' '.join(required))!=actual:
                issues.append('Rendered PDF text differs from planned text or reading order')
            if expected[-1]['kind'] in ('section','subject'):
                issues.append('Orphaned heading')
            if expected[0]['kind']=='bullet' and len(expected)==1:
                issues.append('Isolated continuation bullet')
            if bottom>200:
                warnings.append('Page {}: {} pt bottom space after bounded spacing expansion'.format(index+1,bottom))
            stats.append({'page':index+1,'bottom_space_pt':bottom,'ink_bounds_pt':raster_bounds,
                          'extractable_characters':len(actual)})
    return {'passed':not issues,'issues':issues,'warnings':warnings,'pages':stats,'page_count':len(stats)}


def render(groups,template,title,make_pdf=True):
    pages,scale = paginate(groups,template)
    word = docx_bytes(pages,template,title)
    if not make_pdf:
        return word,None,None
    pdf = pdf_bytes(pages,template,title)
    report = inspect_pdf(pdf,pages,template)
    report['spacing_scale'] = scale
    return word,pdf,report


def page_png(pdf,page_number):
    import fitz
    with fitz.open(stream=pdf,filetype='pdf') as document:
        if 1<=page_number<=document.page_count:
            page = document[page_number-1]
            return page.get_pixmap(matrix=fitz.Matrix(1.45,1.45),alpha=False).tobytes('png')
    return None
