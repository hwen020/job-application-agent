"""Local fixture pages with platform-specific markup and no external requests."""
from html import escape
import json

from .runner_vault import RunnerError

PLATFORMS = ('PageUp','NEOGOV')
PROFILE = {'first_name':'Synthetic','last_name':'Demo Candidate','email':'synthetic.demo@example.invalid',
           'phone':'+1-202-555-0100','city':'Synthetic Demo City'}
REFERENCE = {'name':'Synthetic Demo Reference','email':'reference@example.invalid','phone':'+1-202-555-0101',
             'title':'Example Supervisor','employer':'Synthetic Demo Laboratory'}


def fixture_row(db,key):
    row = db.execute("""SELECT r.*,s.signed_in,s.obstacle,s.receipt,t.state,j.title,j.employer
     FROM runner_runs r JOIN runner_simulation s USING(task_id) JOIN browser_tasks t ON t.id=r.task_id
     JOIN jobs j ON j.id=r.job_id WHERE fixture_key=? AND mode='simulation'""",(key,)).fetchone()
    if not row:
        raise RunnerError('Simulation was not found.')
    return row


def fixture_html(db,key,step):
    row = fixture_row(db,key)
    neo = row['platform']=='NEOGOV'
    mark = 'neogov' if neo else 'pageup'
    state = '<div data-draft>Continue saved application</div>' if json.loads(row['checkpoint']).get('completed') else ''
    if row['receipt']:
        body = '<div data-receipt>'+escape(row['receipt'])+'</div>'
    elif row['obstacle'] in ('captcha','mfa'):
        body = '<div data-security>Manual security verification required</div>'
    elif not row['signed_in']:
        body = '<div data-login>Sign in to continue</div><input type="password" aria-label="Password">'
    elif step=='contact':
        labels = (('first','Legal First Name' if neo else 'First name','text'),
                  ('last','Legal Last Name' if neo else 'Surname','text'),
                  ('email','Email Address' if neo else 'Email','email'),
                  ('phone','Primary Phone' if neo else 'Phone number','tel'),
                  ('resume','Upload resume' if neo else 'Resume / CV','file'),
                  ('cover','Cover letter attachment' if neo else 'Cover Letter','file'))
        body = ''.join(control(mark,*field) for field in labels)
        body += '<a data-next href="?step=questions">Next</a>'
    elif step=='questions':
        body = control(mark,'availability','Work schedule' if neo else 'Which schedule can you work?',
                       'select',('Full time','Part time'))
        body += control(mark,'source','How did you learn about this opening?' if neo else 'How did you hear about this position?',
                        'select',('Institution website','Job fair','Other'))
        if row['obstacle'] in ('reference','reference_ready'):
            for name,label in (('name','Reference name'),('email','Reference email'),('phone','Reference phone'),
                               ('title','Reference title'),('employer','Reference employer')):
                body += control(mark,'ref_'+name,label,'text')
        if row['obstacle']=='sensitive':
            body += control(mark,'sponsor','Will you require H-1B sponsorship in the future?','select',('Yes','No'))
        body += '<a data-next href="?step=review">Review</a>'
    elif step=='review':
        body = """<div data-review>Review application</div><button id="send">Submit simulation</button>
        """
    else:
        raise RunnerError('Simulation step was not found.')
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic Demo {mark} fixture</title>'
            f'<body data-platform="{mark}"><h1>{escape(row["title"])}</h1>'
            f'<p>{escape(row["employer"])}</p>{state}<main data-step="{step}">{body}</main><script src="/runner-fixture.js"></script></body></html>').encode()


def control(platform,key,label,kind,options=()):
    target = platform+'-'+key
    marker = f' id="{target}" name="{key}" required'
    if platform=='pageup':
        text = f'<label for="{target}">{label}</label>'
    else:
        text = f'<span id="{target}-label">{label}</span>'
        marker += f' aria-labelledby="{target}-label"'
    if kind=='select':
        values = ''.join(f'<option value="{escape(option)}">{escape(option)}</option>' for option in options)
        field = f'<select{marker}><option value="">Choose</option>{values}</select>'
    else:
        field = f'<input type="{kind}"{marker}>'
    return '<div>'+text+field+'</div>'


def simulation_receipt(db,key):
    db.execute('BEGIN IMMEDIATE')
    row = fixture_row(db,key)
    if row['receipt']:
        return {'receipt':row['receipt']}
    if not row['submit_intent'] or not row['review_token'] or row['state']!='RUNNING':
        raise RunnerError('Review and confirmation are required.')
    receipt = 'SYNTHETIC-DEMO-{:05d}'.format(row['task_id'])
    db.execute('UPDATE runner_simulation SET receipt=? WHERE task_id=?',(receipt,row['task_id']))
    return {'receipt':receipt}
