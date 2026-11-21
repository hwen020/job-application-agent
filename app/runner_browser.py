"""Only the local Playwright fixture adapter is included in Portfolio Edition."""
import os
from pathlib import Path

from .runner_vault import RunnerError

SITE_SECURITY = 'BLOCKED_BY_SITE_SECURITY'
SITE_SECURITY_MESSAGE = 'The mock browser requires a manual action.'
OFFICIAL_SESSIONS = {}

def launch_options():
    roots = (Path(os.environ.get('ProgramFiles', '')), Path(os.environ.get('ProgramFiles(x86)', '')))
    paths = (roots[0]/'Google/Chrome/Application/chrome.exe',
             roots[1]/'Microsoft/Edge/Application/msedge.exe')
    if os.name == 'nt':
        browser = next((path for path in paths if path.is_file()), None)
        if browser:
            return {'executable_path': str(browser)}
    return {}

def deny_official(*args):
    raise RunnerError('Portfolio Edition only supports the local Synthetic Demo ATS.')

verified_url = open_official = close_official = deny_official

FIELD_MAP = {
    'first name':'first_name','legal first name':'first_name','surname':'last_name','legal last name':'last_name',
    'last name':'last_name','email':'email','email address':'email','phone number':'phone','primary phone':'phone',
    'resume / cv':'resume','upload resume':'resume','cover letter':'cover','cover letter attachment':'cover',
    'reference name':'ref_name','reference email':'ref_email','reference phone':'ref_phone',
    'reference title':'ref_title','reference employer':'ref_employer',
}
ALIASES = {
    'how did you hear about this position?':'how did you hear about this position?',
    'how did you learn about this opening?':'how did you hear about this position?',
    'which schedule can you work?':'which schedule can you work?',
    'work schedule':'which schedule can you work?',
}

DISCOVER = """elements => elements.filter(el => el.type !== 'password' && el.type !== 'hidden')
.map(el => {
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
        .map(id => document.getElementById(id)?.textContent || '').join(' ');
    const label = labelled || el.getAttribute('aria-label') || [...el.labels || []].map(item => item.textContent).join(' ');
    const options = el.tagName === 'SELECT' ? [...el.options].filter(option => option.value && !option.disabled)
        .map(option => ({id:option.value,label:option.textContent.trim()})) : [];
    return {id:el.id,question:label.trim(),kind:el.tagName==='SELECT'?'select':el.type,
        required:el.required,options};
})"""


class SimulationBrowser:
    def __init__(self,context,base,key):
        self.context = context
        self.page = context.new_page()
        self.url = base+'/runner-sim/'+key

    def visit(self,step):
        self.page.goto(self.url+'?step='+step,wait_until='domcontentloaded',timeout=7000)
        if self.page.locator('[data-security]').count():
            return 'security'
        if self.page.locator('[data-login]').count():
            return 'login'
        if self.page.locator('[data-receipt]').count():
            return 'submitted'
        return 'draft' if self.page.locator('[data-draft]').count() else 'form'

    def fields(self):
        return self.page.locator('main input,main select,main textarea').evaluate_all(DISCOVER)

    def fill(self,field,value):
        element = self.page.locator('[id='+repr(field['id'])+']')
        if field['kind']=='file':
            element.set_input_files({'name':value['name'],'mimeType':'application/pdf','buffer':value['data']})
            actual = element.evaluate('''async (element) => {
                const file = element.files[0];
                if (!file) return null;
                const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()));
                return {name:file.name, sha256:[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('')};
            }''')
            return actual=={'name':value['name'],'sha256':value['sha256']}
        if field['kind']=='select':
            element.select_option(value)
        else:
            element.fill(value)
        return element.input_value()==value

    def advance(self,step):
        self.page.locator('[data-next]').click()
        self.page.locator('[data-step="'+step+'"]').wait_for(timeout=7000)

    def review_fields(self):
        return self.page.locator('[data-answer-id]').evaluate_all(
            'elements => Object.fromEntries(elements.map(el => [el.dataset.answerId,el.textContent]))')

    def submit(self):
        self.page.get_by_role('button',name='Submit simulation').click()
        return self.page.locator('[data-receipt]').inner_text(timeout=7000)
