from contextlib import closing
from http.client import HTTPConnection
from io import BytesIO
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from docx import Document
from pypdf import PdfReader

from app.server import DemoServer


class DemoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.server = DemoServer(('127.0.0.1',0), Path(temp.name)/'demo.sqlite3')
        self.addCleanup(self.server.server_close)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.cookie = ''
        self.request('/api/dashboard')

    def request(self, path, payload=None, status=200, headers=None):
        with closing(HTTPConnection('127.0.0.1', self.server.server_port, timeout=40)) as client:
            client.request('GET' if payload is None else 'POST', path, None if payload is None else json.dumps(payload),
                           {'Content-Type':'application/json','Cookie':self.cookie,**(headers or {})})
            response = client.getresponse()
            raw = response.read()
            cookie = response.getheader('Set-Cookie')
            if cookie:
                self.cookie = cookie.split(';',1)[0]
            self.assertEqual(response.status, status, raw[:250])
            return json.loads(raw) if response.getheader('Content-Type').startswith('application/json') else raw

    def prepare(self, job):
        job_id = job['id']
        parsed = self.request('/api/demo/parse', {'job_id': job_id})
        self.assertGreater(parsed['coverage']['with_evidence'],0)
        self.assertTrue(parsed['eligibility_checks']['experience_years']['requirements'])
        evidence = parsed['candidates']
        self.assertTrue(evidence[0]['sources'])
        args = {'job_id': job_id,'jd_id':parsed['jd']['id']}
        self.request('/api/demo/generate',{**args,'fact_ids':[4]},status=400)
        self.request('/api/demo/tasks',{'job_id':job_id},status=400)
        version = self.request('/api/demo/generate',{**args,'fact_ids':[item['fact_id'] for item in evidence[:2]]})
        pdf = PdfReader(BytesIO(self.request(f"/api/artifacts/{version['id']}/Resume.pdf")))
        cover = Document(BytesIO(self.request(f"/api/artifacts/{version['id']}/Cover_Letter.docx")))
        self.assertIn('Synthetic Demo Candidate', ''.join(page.extract_text() for page in pdf.pages))
        self.assertIn('Synthetic Demo Candidate', '\n'.join(item.text for item in cover.paragraphs))
        approval = {'version_id':version['id']}
        self.request('/api/demo/approve',approval,status=400)
        approved = self.request('/api/demo/approve',{**approval,'confirmed':True})
        self.assertTrue(approved['approved_at'])

    def walk_mock(self, job):
        self.prepare(job)
        task = self.request('/api/demo/tasks',{'job_id':job['id']})
        self.assertEqual(task['platform'],job['platform'])
        key = {'task_id':task['task_id']}
        control = '/api/demo/control'
        paused = self.request(control,{**key,'action':'run'})
        self.assertEqual(paused['state'],'WAITING_FOR_USER',paused)
        self.assertEqual(len(paused['checkpoint']['files']),2)
        fields = paused['checkpoint']['pending']
        self.assertEqual(len(fields),2)
        for field in fields:
            answer = {**key,'field_id':field['id'],'confirmed':True}
            self.request('/api/demo/answer',{**answer,'answer':'synthetic@example.invalid'},status=400)
            self.request('/api/demo/answer',{**answer,'answer':field['options'][0]['id']})
        self.assertEqual(self.request(control,{**key,'action':'pause'})['state'],'PAUSED')
        saved = self.request('/api/demo/job/'+str(job['id']))
        self.assertEqual(saved['task']['state'],'PAUSED')
        ready = self.request(control,{**key,'action':'run'})
        self.assertEqual(ready['state'],'READY_FOR_REVIEW',ready)
        self.assertTrue(ready['checkpoint']['draft_found'])
        submit = {**key,'action':'submit','confirmed':True}
        self.request(control,submit,status=400)
        done = self.request(control,{**submit,'review_token':ready['review_token']})
        self.assertEqual(done['state'],'SUBMITTED',done)
        self.assertTrue(done['submitted_receipt'].startswith('SYNTHETIC-DEMO-'))
        self.request(control,{**submit,'review_token':ready['review_token']},status=400)
        self.request('/api/demo/tasks',{'job_id':job['id']},status=400)

    def test_both_mock_ats_and_repeatable_reset(self):
        start = self.request('/api/dashboard')
        for job in start['jobs']:
            with self.subTest(platform=job['platform']):
                self.walk_mock(job)
        end = self.request('/api/dashboard')
        self.assertEqual(end['reports']['simulated_receipts'],2)
        self.assertTrue(all(job['status']=='Synthetic Demo Submitted' for job in end['jobs']))
        self.assertEqual(sum(event['action']=='synthetic_receipt_recorded' for event in end['audit']),2)
        self.request('/api/demo/reset',{},status=400)
        self.request('/api/demo/reset',{'confirmed':True})
        self.assertEqual(self.request('/api/dashboard')['reports'],start['reports'])

    def test_public_surface_rejects_live_and_external_actions(self):
        payload = {'confirmed': True,'url':'https://example.invalid/official'}
        for route in ('cloud/outlook/start','cloud/oauth/configure','cloud/drive/archive','cloud/sheets/commit',
                      'cloud/email/scan','cloud/live','runner/profile','runner/references','jobs','runner/control'):
            self.request('/api/'+route,payload,status=403)
        self.request('/api/demo/tasks',{'job_id':1,'mode':'official'},status=400)
        self.request('/api/demo/reset',{'confirmed':True},status=400,headers={'Origin':'https://example.invalid'})
        self.request('/api/runner/mock-receipt',{'key':'fake'},status=403,headers={'Host':'example.invalid'})
        self.request('/api/runner/mock-receipt',{'key':'fake'},status=403)
        self.request('/runtime/portfolio.sqlite3',status=404)
        self.request('/.env',status=404)

    def test_healthz_is_read_only_and_independent_of_workflow_lock(self):
        before = self.request('/api/dashboard')
        with self.server.lock:
            self.assertEqual(self.request('/healthz',headers={'Host':'example.invalid'}),{'status':'ok'})
        self.assertEqual(self.request('/api/dashboard'),before)
        with self.server.db() as db:
            db.execute('ALTER TABLE jobs RENAME TO temporary_jobs')
        self.assertEqual(self.request('/healthz',status=503),{'status':'unavailable'})

    def test_pipeline_reconciles_a_committed_receipt_once(self):
        self.prepare(self.request('/api/dashboard')['jobs'][0])
        task = self.request('/api/demo/tasks',{'job_id':1})
        visitor = self.server.visitors[self.cookie.split('=',1)[1]]
        with self.server.db(visitor.path) as db:
            db.execute("UPDATE runner_runs SET submitted_receipt='SYNTHETIC-DEMO-RECOVERY' WHERE task_id=?",(task['task_id'],))
        first = self.request('/api/dashboard')
        second = self.request('/api/dashboard')
        self.assertEqual(first,second)
        self.assertEqual(second['reports']['simulated_receipts'],1)

    def test_two_browser_contexts_cannot_share_application_data(self):
        from playwright.sync_api import sync_playwright
        from app.runner_browser import launch_options

        self.prepare(self.request('/api/dashboard')['jobs'][0])
        task = self.request('/api/demo/tasks',{'job_id':1})
        alpha_cookie = self.cookie.split('=',1)[1]
        base = self.server.fixture_base
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_options())
            try:
                alpha, beta = [browser.new_context() for _ in range(2)]
                alpha.add_cookies([{'name':'portfolio_session','value':alpha_cookie,'url':base}])
                self.assertEqual(beta.request.get(base+'/api/dashboard').status,200)
                artifact = base+'/api/artifacts/1/Resume.pdf'
                target = base+'/api/demo/tasks/'+str(task['task_id'])
                self.assertEqual(beta.request.get(artifact).status,404)
                self.assertEqual(beta.request.get(target).status,404)
                self.assertEqual(beta.request.post(base+'/api/demo/approve',
                                                   data={'version_id':1,'confirmed':True}).status,400)
                self.assertEqual(beta.request.post(base+'/api/demo/reset',data={'confirmed':True}).status,200)
                self.assertEqual(alpha.request.get(artifact).status,200)
                self.assertEqual(alpha.request.get(target).status,200)
            finally:
                browser.close()
