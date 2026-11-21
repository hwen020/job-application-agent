"""Checkpointed Playwright runner. Only loopback fixtures can submit."""
import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from threading import RLock

from . import materials, review, runner_browser, runner_fixtures, runner_vault
from .imports import clean
from .runner_vault import RunnerError
from .storage import normalized

LOCK = RLock()
TERMINAL = {'CANCELLED','SUBMITTED'}
STEPS = {'session':1,'contact':2,'questions':3,'review':4,'submission':5}
SCOPES = {'session','employer','platform','global'}
SENSITIVE = re.compile(r'\b(?:opt|stem opt|h.?1b|citizenship|nationality|ethnicity|disability|'
                       r'veteran|sponsor\w*|visa|work authori[sz]ation|race|gender|criminal|'
                       r'password|captcha|verification code|social security|ssn)\b',re.I)
OFF_LIMITS = re.compile(r'\b(?:password|captcha|verification code|social security|ssn)\b',re.I)


def event(db,task,action,detail):
    db.execute('INSERT INTO runner_events(task_id,action,detail) VALUES(?,?,?)',(task,action,detail))


def state(db,task,value,detail):
    db.execute('UPDATE browser_tasks SET state=? WHERE id=?',(value,task))
    event(db,task,value,detail)


def step(db,task,name,value):
    db.execute('INSERT INTO task_steps(task_id,position,state) VALUES(?,?,?) '
               'ON CONFLICT(task_id,position) DO UPDATE SET state=excluded.state',(task,STEPS[name],value))
    event(db,task,'STEP',name+' '+value)


def run_row(db,task):
    if type(task) is not int:
        raise RunnerError('Invalid task ID.')
    row = db.execute("""SELECT r.*,t.state,j.title,j.employer,j.url,j.status,j.submitted_at
        FROM runner_runs r JOIN browser_tasks t ON t.id=r.task_id JOIN jobs j ON j.id=r.job_id
        WHERE r.task_id=?""",(task,)).fetchone()
    if not row:
        raise RunnerError('Application task was not found.')
    return row


def view(db,task):
    row = run_row(db,task)
    result = {k:row[k] for k in ('task_id','job_id','state','platform','mode','title','employer','review_token','submitted_receipt')}
    result['checkpoint'] = json.loads(row['checkpoint'])
    if row['mode']=='official' and row['state'] not in TERMINAL:
        session = runner_browser.OFFICIAL_SESSIONS.get(task)
        result['inspection_available'] = bool(session and session[0].is_alive())
        try:
            result['manual_url'] = runner_browser.verified_url(row['url'],row['platform'])
        except RunnerError:
            pass
    result['steps'] = [{**dict(r),'name':name} for name,pos in STEPS.items()
                       for r in db.execute('SELECT position,state FROM task_steps WHERE task_id=? AND position=?',(task,pos))]
    result['events'] = [dict(r) for r in db.execute(
        'SELECT action,detail,created_at FROM runner_events WHERE task_id=? ORDER BY id',(task,))]
    return result


def mark_site_security(db,row):
    checkpoint = json.loads(row['checkpoint'])
    if checkpoint.get('page')==runner_browser.SITE_SECURITY and row['state']=='WAITING_FOR_USER':
        return
    checkpoint.update(page=runner_browser.SITE_SECURITY,message=runner_browser.SITE_SECURITY_MESSAGE,pending=[])
    checkpoint_write(db,row,checkpoint)
    step(db,row['task_id'],'session',runner_browser.SITE_SECURITY)
    state(db,row['task_id'],'WAITING_FOR_USER',runner_browser.SITE_SECURITY)



def listing(db):
    return [{'task_id':r['task_id'],'job_id':r['job_id'],'title':r['title'],'employer':r['employer'],
             'state':r['state'],'mode':r['mode'],'platform':r['platform']} for r in db.execute(
        'SELECT r.*,j.title,j.employer,t.state FROM runner_runs r JOIN jobs j ON j.id=r.job_id '
        'JOIN browser_tasks t ON t.id=r.task_id ORDER BY r.task_id DESC')]


def create(db,p):
    job = db.execute('SELECT * FROM jobs WHERE id=?',(p['job_id'],)).fetchone()
    platform,mode = p['platform'],p.get('mode','simulation')
    if not job or platform not in runner_fixtures.PLATFORMS or mode not in ('simulation','official'):
        raise RunnerError('Choose a Pipeline job and a supported platform.')
    if mode=='official':
        runner_browser.verified_url(job['url'],platform)
    db.execute('BEGIN IMMEDIATE')
    if mode=='official' and (job['submitted_at'] or job['status']=='Submitted'):
        raise RunnerError('The Pipeline already records a submission for this job.')
    current = db.execute("""SELECT r.task_id,t.state FROM runner_runs r JOIN browser_tasks t ON t.id=r.task_id
        WHERE r.job_id=? AND r.mode=? AND (t.state!='CANCELLED' OR r.submit_intent=1) ORDER BY r.task_id DESC LIMIT 1""",
                         (job['id'],mode)).fetchone()
    if current:
        if current['state']=='SUBMITTED':
            raise RunnerError('This job already has a submission in this environment.')
        return view(db,current['task_id'])
    db.execute('INSERT OR IGNORE INTO applications(job_id,status) VALUES(?,?)',(job['id'],job['status'] or 'To review'))
    application = db.execute('SELECT id FROM applications WHERE job_id=?',(job['id'],)).fetchone()[0]
    task = db.execute("INSERT INTO browser_tasks(application_id,state) VALUES(?,'CREATED')",(application,)).lastrowid
    db.execute('INSERT INTO runner_runs(task_id,job_id,platform,mode,fixture_key) VALUES(?,?,?,?,?)',
               (task,job['id'],platform,mode,secrets.token_urlsafe(24)))
    if mode=='simulation':
        db.execute('INSERT INTO runner_simulation(task_id) VALUES(?)',(task,))
    event(db,task,'CREATED','Created '+mode+' task')
    return view(db,task)


def simulation_controls(db,p):
    db.execute('BEGIN IMMEDIATE')
    row = run_row(db,p['task_id'])
    obstacle = p.get('obstacle','')
    if row['mode']!='simulation' or row['state'] in TERMINAL | {'RUNNING'} or row['submit_intent']:
        raise RunnerError('Simulator controls are unavailable in this state.')
    if obstacle not in ('','offline','captcha','mfa','reference','reference_ready','sensitive'):
        raise RunnerError('Unknown simulation scenario.')
    if type(p.get('signed_in')) is not bool:
        raise RunnerError('Explicitly set mock login state.')
    db.execute('UPDATE runner_simulation SET signed_in=?,obstacle=? WHERE task_id=?',
               (int(p['signed_in']),obstacle,row['task_id']))
    db.execute('UPDATE runner_runs SET review_token=NULL WHERE task_id=?',(row['task_id'],))
    if row['state']=='READY_FOR_REVIEW':
        state(db,row['task_id'],'PAUSED','Simulator settings changed; refresh the review.')
    event(db,row['task_id'],'SIMULATION','Updated mock login and scenario')
    return view(db,row['task_id'])


def context_owner(row,scope):
    value = {'session':str(row['task_id']),'employer':normalized(row['employer']),
             'platform':normalized(row['platform']),'global':'*'}[scope]
    return row['mode']+':'+value


def sensitive(question):
    return bool(SENSITIVE.search(question) or review.requires_review(normalized(question)))


def question_key(question,is_sensitive):
    key = normalized(question)
    return key if is_sensitive else runner_browser.ALIASES.get(key,key)


def confirmed_answer(db,row,field,is_sensitive):
    question = question_key(field['question'],is_sensitive)
    today = datetime.now(timezone.utc).date().isoformat()
    for scope in ('session','employer','platform','global'):
        rule = db.execute("""SELECT rr.*,a.answer,p.ciphertext FROM runner_rules rr
          LEFT JOIN answer_rules a ON a.id=rr.rule_id LEFT JOIN runner_private p ON p.id=rr.private_id
          WHERE rr.question=? AND rr.scope=? AND rr.owner=? AND (rr.expires_at IS NULL OR rr.expires_at>=?)""",
                          (question,scope,context_owner(row,scope),today)).fetchone()
        if rule:
            value = runner_vault.private_read(rule)['answer'] if rule['private_id'] else rule['answer']
            return {'answer':value,'rule_id':rule['rule_id'],'runner_rule_id':rule['id'],
                    'revision':rule['private_id'] or rule['rule_id'],'scope':scope,'confidence':1 if is_sensitive else .98}
    if not is_sensitive:
        match = review.match_rule(db,{'question':question,'platform':row['platform'],'employer':row['employer']})
        if match['state']=='matched':
            rule = match['rule']
            if row['mode']!='simulation' or field['options'] and rule['answer'] in [op['id'] for op in field['options']]:
                return {'answer':rule['answer'],'rule_id':rule['id'],'runner_rule_id':None,
                        'revision':rule['id'],'scope':rule['scope'],'confidence':.95}
    return None


def suggestion(db,row,field):
    question = question_key(field['question'],False)
    result = review.match_rule(db,{'question':question,'platform':row['platform'],'employer':row['employer']})
    if result['state']=='matched':
        rule = result['rule']
        equivalent = [op['id'] for op in field['options'] if normalized(op['id'])==normalized(rule['answer'])]
        if len(equivalent)==1 and equivalent[0]!=rule['answer']:
            return equivalent[0], 'Confirmed rule #{} uses different casing or whitespace; verify this option.'.format(rule['id'])
    return None, 'No verified suggestion matches the current wording and options.'


def confirm_question(db,p):
    db.execute('BEGIN IMMEDIATE')
    row = run_row(db,p['task_id'])
    if row['state']!='WAITING_FOR_USER' or p.get('confirmed') is not True:
        raise RunnerError('Confirm an unanswered field from the current paused page.')
    fields = json.loads(row['checkpoint']).get('pending',[])
    field = next((f for f in fields if f['id']==p['field_id'] and f.get('confirmable')),None)
    scope = p['scope']
    if not field or scope not in SCOPES:
        raise RunnerError('Unknown question or rule scope.')
    answer = clean(p['answer'],240)
    options = [item['id'] for item in field['options']]
    if not answer or options and answer not in options:
        raise RunnerError('Select an option available on the current page.')
    protection = field['sensitive']
    expiry = p.get('expires_at') or None
    if protection:
        today = datetime.now(timezone.utc).date()
        try:
            until = date.fromisoformat(expiry or '')
        except ValueError as error:
            raise RunnerError('A sensitive confirmation requires a current expiry date.') from error
        if scope=='global' or not today<=until<=today+timedelta(days=365):
            raise RunnerError('Use a limited scope and an expiry within one year.')
    elif expiry:
        try:
            date.fromisoformat(expiry)
        except ValueError as error:
            raise RunnerError('Invalid expiry date.') from error
    key = question_key(field['question'],protection)
    owner = context_owner(row,scope)
    rule_id = private_id = None
    if protection or not options:
        private_id = runner_vault.private_write(db,'answer',None,{'answer':answer})
    else:
        # Mock rules are visible as confirmed dynamic entries; the Phase 1 matcher cannot apply them to real jobs.
        store_scope = 'dynamic'
        version = db.execute("""SELECT COALESCE(MAX(version),0)+1 FROM answer_rules
                                  WHERE question=? AND scope=? AND platform=? AND employer=?""",
                             (key,store_scope,row['platform'],row['employer'])).fetchone()[0]
        rule_id = db.execute("""INSERT INTO answer_rules(question,answer,scope,platform,employer,valid_until,version)
                            VALUES(?,?,?,?,?,?,?)""",(key,answer,store_scope,row['platform'],row['employer'],expiry,version)).lastrowid
        db.execute('INSERT INTO rule_confirmations(rule_id) VALUES(?)',(rule_id,))
    db.execute("""INSERT INTO runner_rules(question,scope,owner,rule_id,private_id,expires_at) VALUES(?,?,?,?,?,?)
         ON CONFLICT(question,scope,owner) DO UPDATE SET rule_id=excluded.rule_id,private_id=excluded.private_id,
         expires_at=excluded.expires_at,confirmed_at=CURRENT_TIMESTAMP""",(key,scope,owner,rule_id,private_id,expiry))
    event(db,row['task_id'],'RULE_CONFIRMED','Scope '+scope+'; protected' if protection else 'Scope '+scope)
    return view(db,row['task_id'])


def approved_files(db,job):
    final = db.execute("""SELECT v.id FROM material_final f JOIN material_versions v ON v.id=f.version_id
        WHERE f.job_id=? AND v.job_id=f.job_id AND v.approved_at IS NOT NULL""",(job,)).fetchone()
    files = {}
    if final:
        for key,name in (('resume','Resume.pdf'),('cover','Cover_Letter.pdf')):
            content = materials.artifact(db,final['id'],name)
            if content:
                files[key] = {'name':name,'data':content,'version_id':final['id'],
                              'sha256':hashlib.sha256(content).hexdigest()}
    return files


def fill_page(db,row,browser,page,checkpoint,mock):
    files = approved_files(db,row['job_id'])
    for field in browser.fields():
        label = field['question']
        key = runner_browser.FIELD_MAP.get(normalized(label))
        protection = sensitive(label)
        reference = key and key.startswith('ref_')
        blocked = OFF_LIMITS.search(label)
        mapping = {**field,'platform':row['platform'],'employer':row['employer'],'page':page,
                   'rule_id':None,'runner_rule_id':None,'scope':None,'confidence':0,
                   'sensitive':bool(protection or reference),'needs_confirmation':True}
        reason = 'No confirmed answer applies to this exact question and context.'
        value = None
        if blocked or not label:
            reason = 'Manual handling is required for this field.'
        elif key in ('resume','cover'):
            value = files.get(key)
            reason = 'Approve the current job material in Materials Studio.'
        elif reference:
            value = runner_fixtures.REFERENCE.get(key[4:]) if mock['obstacle']=='reference_ready' else None
            reason = 'A reference who gave permission is required.'
        elif key in runner_fixtures.PROFILE:
            value = runner_fixtures.PROFILE[key]
        else:
            confirmation = confirmed_answer(db,row,field,protection)
            if confirmation:
                value = confirmation.pop('answer')
                mapping.update(confirmation)
        valid = field['kind']!='select' or value in [item['id'] for item in field['options']]
        if value and valid and browser.fill(field,value):
            mapping.update(needs_confirmation=False,confidence=mapping['confidence'] or 1)
            if reference:
                mapping['scope'] = 'simulation reference consent'
            elif key in runner_fixtures.PROFILE:
                mapping['scope'] = 'synthetic applicant profile'
            if key in ('resume','cover'):
                file = {k:v for k,v in value.items() if k!='data'}
                checkpoint['files'].append(file)
                mapping['answer'] = value['name']
            else:
                mapping['answer'] = 'Protected answer confirmed' if protection or reference else value
            checkpoint['completed'].append(mapping)
        elif field['required'] or value:
            confirmable = bool(label and not key and not blocked and field['kind'] in ('text','select'))
            recommended,basis = (suggestion(db,row,field) if confirmable and not protection else
                                 (None,'No verified suggestion matches the current wording and options.'))
            mapping.update(reason=reason,confirmable=confirmable,suggested_answer=recommended,suggestion_basis=basis)
            checkpoint['pending'].append(mapping)


def checkpoint_write(db,row,data,review_token=None):
    db.execute('UPDATE runner_runs SET checkpoint=?,review_token=? WHERE task_id=?',
               (json.dumps(data,ensure_ascii=False,sort_keys=True),review_token,row['task_id']))


def finish_receipt(db,row,receipt):
    db.execute('UPDATE runner_runs SET submitted_receipt=?,submit_intent=0 WHERE task_id=?',(receipt,row['task_id']))
    step(db,row['task_id'],'submission','DONE')
    state(db,row['task_id'],'SUBMITTED','Simulation receipt recorded; real Pipeline status unchanged.')


def mock_execute(db,row,base,requested_token=None):
    from playwright.sync_api import sync_playwright
    task = row['task_id']
    with db:
        db.execute('BEGIN IMMEDIATE')
        mock = db.execute('SELECT * FROM runner_simulation WHERE task_id=?',(task,)).fetchone()
        if not mock['receipt'] and row['submit_intent'] and not requested_token:
            db.execute('UPDATE runner_runs SET submit_intent=0 WHERE task_id=?',(task,))
    if mock['receipt']:
        with db:
            finish_receipt(db,row,mock['receipt'])
        return
    checkpoint = {'page':'session','completed':[],'pending':[],'files':[],'message':''}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,**runner_browser.launch_options())
        try:
            context = browser.new_context()
            def only_fixture(route):
                if route.request.url.startswith(base+'/'):
                    route.continue_()
                else:
                    route.abort()
            context.route('**/*',only_fixture)
            agent = runner_browser.SimulationBrowser(context,base,row['fixture_key'])
            if mock['obstacle']=='offline':
                context.set_offline(True)
            status = agent.visit('contact')
            checkpoint['draft_found'] = status=='draft'
            if status in ('security','login'):
                checkpoint['message'] = 'Manual CAPTCHA/MFA verification required.' if status=='security' else 'Mock browser is signed out.'
                with db:
                    checkpoint_write(db,row,checkpoint)
                    step(db,task,'session','WAITING')
                    state(db,task,'WAITING_FOR_USER','Security or login requires a user action.')
                return
            with db:
                step(db,task,'session','DONE')
            for page in ('contact','questions'):
                if page!='contact':
                    agent.advance(page)
                checkpoint['page'] = page
                fill_page(db,row,agent,page,checkpoint,mock)
                with db:
                    step(db,task,page,'WAITING' if checkpoint['pending'] else 'DONE')
                    if not requested_token:
                        checkpoint_write(db,row,checkpoint)
                    if checkpoint['pending']:
                        checkpoint_write(db,row,checkpoint)
                        state(db,task,'WAITING_FOR_USER','Required fields or evidence need review.')
                        return
            agent.advance('review')
            if agent.page.locator('[data-review]').count()!=1:
                raise RunnerError('Expected review page was unavailable.')
            expected = {field['id'] for field in checkpoint['completed']}
            actual = agent.review_fields()
            if expected!=set(actual) or any(actual[field['id']]!=field['answer']
                    for field in checkpoint['completed'] if field['answer']!='Protected answer confirmed'):
                raise RunnerError('Fixture review does not match filled fields.')
            checkpoint['page'] = 'review'
            checkpoint['message'] = 'Review all completed fields and attachments before confirming.'
            signed = {k:v for k,v in checkpoint.items() if k not in ('draft_found','message')}
            token = hashlib.sha256(json.dumps(signed,sort_keys=True).encode()).hexdigest()
            with db:
                step(db,task,'review','DONE')
                checkpoint_write(db,row,checkpoint,token)
                if not requested_token or token!=requested_token:
                    state(db,task,'READY_FOR_REVIEW','Review prepared.' if not requested_token else 'Review changed; confirmation required again.')
                    return
                current = approved_files(db,row['job_id'])
                latest_files = [{k:v for k,v in item.items() if k!='data'} for item in current.values()]
                if latest_files!=checkpoint['files']:
                    checkpoint['message'] = 'Approved attachments changed. Resume and review the new version.'
                    checkpoint_write(db,row,checkpoint)
                    state(db,task,'PAUSED','Approved attachments changed before submission.')
                    return
                db.execute('UPDATE runner_runs SET submit_intent=1 WHERE task_id=?',(task,))
                step(db,task,'submission','RUNNING')
            receipt = agent.submit()
            with db:
                finish_receipt(db,row,receipt)
        finally:
            browser.close()


def control(db,p,base):
    task,action = p['task_id'],p['action']
    with LOCK:
        row = run_row(db,task)
        old = row['state']
        if old in TERMINAL:
            raise RunnerError('Task is already closed.')
        if action in ('pause','cancel'):
            if row['submit_intent']:
                raise RunnerError('Recover a pending submission before closing the task.')
            with db:
                state(db,task,'PAUSED' if action=='pause' else 'CANCELLED','User requested '+action)
            if action=='cancel' and row['mode']=='official':
                runner_browser.close_official(task)
            return view(db,task)
        if action not in ('run','submit'):
            raise RunnerError('Unknown task operation.')
        if (action=='run' and row['mode']=='official'
                and json.loads(row['checkpoint']).get('page')==runner_browser.SITE_SECURITY):
            return view(db,task)
        requested = None
        if action=='submit':
            if (row['mode']!='simulation' or old!='READY_FOR_REVIEW' or p.get('confirmed') is not True
                    or not row['review_token'] or not secrets.compare_digest(row['review_token'],str(p.get('review_token','')))):
                raise RunnerError('Current review and explicit confirmation are required.')
            requested = row['review_token']
        with db:
            state(db,task,'RUNNING','Resume from saved checkpoint.' if old!='CREATED' else 'Started task.')
        try:
            if row['mode']=='simulation':
                mock_execute(db,row,base,requested)
            else:
                if row['submitted_at'] or row['status']=='Submitted':
                    raise RunnerError('The Pipeline already records a submission for this job.')
                message = runner_browser.open_official(row['url'],row['platform'],task)
                if isinstance(message,dict) and message.get('state')==runner_browser.SITE_SECURITY:
                    with db:
                        mark_site_security(db,row)
                    return view(db,task)
                with db:
                    previous = json.loads(row['checkpoint'])
                    checkpoint_write(db,row,{'page':'official','message':message,
                        'completed':previous.get('completed',[]),'pending':[],'files':previous.get('files',[])})
                    state(db,task,'WAITING_FOR_USER','Official browser inspection only; user verification needed.')
        except Exception:
            with db:
                event(db,task,'ERROR','Playwright, navigation, or verification failed; inspect the session and retry.')
                state(db,task,'FAILED','Retry will check for an existing receipt before continuing.')
        return view(db,task)
