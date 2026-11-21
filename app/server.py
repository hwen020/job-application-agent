"""Portfolio-only HTTP API with a closed route and fixture allowlist."""
from contextlib import closing, contextmanager
from collections import deque
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import BoundedSemaphore, RLock
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from . import materials, review, runner, runner_fixtures, seed, storage
from .providers import providers

ROOT = Path(__file__).resolve().parent.parent
ASSETS = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'application/javascript'),
          '/style.css': ('style.css', 'text/css'), '/runner-fixture.js': ('runner-fixture.js', 'application/javascript')}
SESSION_TTL = 1800
SESSION_LIMIT = 64


class SessionUnavailable(ValueError):
    pass


class Visitor:
    def __init__(self, directory, now):
        self.directory = TemporaryDirectory(dir=directory)
        self.path = Path(self.directory.name)/'demo.sqlite3'
        self.lock = RLock()
        self.touched = now
        self.actions = deque()


class DemoServer(ThreadingHTTPServer):
    def __init__(self, address, path):
        self.path = path
        self.lock = RLock()
        self.visitors = {}
        self.fixtures = {}
        self.allocations = deque()
        self.fixture_secret = secrets.token_urlsafe(32)
        self.browser_slot = BoundedSemaphore(1)
        self.connections = BoundedSemaphore(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            seed.initialize(db)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.connections.release()

    @contextmanager
    def visitor(self, token, create):
        with self.lock:
            now = monotonic()
            for key, old in list(self.visitors.items()):
                if now-old.touched >= SESSION_TTL and old.lock.acquire(blocking=False):
                    try:
                        old.directory.cleanup()
                        del self.visitors[key]
                    finally:
                        old.lock.release()
            session = self.visitors.get(token)
            fresh = session is None
            if fresh:
                if not create:
                    raise SessionUnavailable('Open the dashboard to start a demo session.')
                self.prune(self.allocations, now)
                if len(self.visitors)>=SESSION_LIMIT or len(self.allocations)>=24:
                    raise SessionUnavailable('Demo capacity reached. Try again later.')
                token = secrets.token_urlsafe(32)
                session = Visitor(self.path.parent, now)
                try:
                    with self.db(session.path) as db:
                        seed.initialize(db)
                except Exception:
                    session.directory.cleanup()
                    raise
                self.visitors[token] = session
                self.allocations.append(now)
            if not session.lock.acquire(blocking=False):
                raise SessionUnavailable('A request is already running for this session.')
            session.touched = now
        try:
            yield session, token if fresh else None
        finally:
            session.lock.release()

    @staticmethod
    def prune(history, now):
        while history and now-history[0]>=60:
            history.popleft()

    def server_close(self):
        super().server_close()
        with self.lock:
            for visitor in self.visitors.values():
                visitor.directory.cleanup()

    @contextmanager
    def db(self, path=None):
        with closing(sqlite3.connect(path or self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            yield db

    @property
    def fixture_base(self):
        return f'http://127.0.0.1:{self.server_port}'

    def reset(self, path):
        with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            fresh = Path(handle.name)
        try:
            with self.db(fresh) as db:
                seed.initialize(db)
            os.replace(fresh, path)
        finally:
            if fresh.exists():
                fresh.unlink()


def summary(db):
    reconcile_receipts(db)
    jobs = [dict(row) for row in db.execute('SELECT id,title,employer,platform,status FROM jobs ORDER BY id')]
    audit = [dict(row) for row in db.execute('SELECT action,entity,detail,created_at FROM audit_events ORDER BY id DESC LIMIT 30')]
    return {'jobs': jobs, 'audit': audit, 'providers': providers.snapshots(), 'candidate': seed.CANDIDATE,
            'reports': {'openings': len(jobs), 'simulated_receipts': sum(job['status']=='Synthetic Demo Submitted' for job in jobs),
                        'approved_versions': db.execute('SELECT COUNT(*) FROM material_versions WHERE approved_at IS NOT NULL').fetchone()[0]}}


def parse_demo(db, job_id):
    row = db.execute('SELECT body FROM demo_jds WHERE job_id=?', (job_id,)).fetchone()
    if not row:
        raise ValueError('Choose a Synthetic Demo job.')
    jd = materials.save_jd(db, {'job_id': job_id, 'text': row['body']})
    result = materials.recommendations(db, {'job_id': job_id, 'jd_id': jd['id']})
    matches = result['matches']
    result['coverage'] = {'with_evidence': sum(bool(match['fact_ids']) for match in matches), 'total': len(matches)}
    return {'jd': jd, 'text': row['body'], **result}


def demo_task(db, task_id):
    task = runner.run_row(db, task_id)
    if task['mode'] != 'simulation':
        raise ValueError('Portfolio Edition runs only the local mock ATS.')
    return task


def reconcile_receipts(db):
    rows = db.execute("""SELECT r.job_id,r.submitted_receipt FROM runner_runs r JOIN jobs j ON j.id=r.job_id
                         WHERE r.mode='simulation' AND r.submitted_receipt IS NOT NULL
                         AND j.status!='Synthetic Demo Submitted'""").fetchall()
    for row in rows:
        job_id = row['job_id']
        db.execute("UPDATE jobs SET status='Synthetic Demo Submitted',submitted_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        db.execute("UPDATE applications SET status='Synthetic Demo Submitted' WHERE job_id=?", (job_id,))
        storage.audit(db, 'synthetic_receipt_recorded', 'job', job_id, row['submitted_receipt'])


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(12)
        super().setup()

    def visitor_cookie(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie',''))
        except CookieError:
            return None
        return jar['portfolio_session'].value if 'portfolio_session' in jar else None

    def respond(self, status, result, mime='application/json'):
        body = result if isinstance(result, bytes) else json.dumps(result).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime + ('' if mime.startswith('application/pdf') else '; charset=utf-8'))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        token = getattr(self, 'new_visitor_token', None)
        if token:
            secure = '; Secure' if self.server.server_address[0]=='0.0.0.0' else ''
            self.send_header('Set-Cookie', f'portfolio_session={token}; Path=/; HttpOnly; SameSite=Strict{secure}')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def log_request(self, code='-', size='-'):
        self.log_message('%s %s', self.command, code)

    def local_fixture(self):
        return (self.client_address[0]=='127.0.0.1'
                and self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'
                and secrets.compare_digest(self.headers.get('X-Demo-Fixture','').encode(),self.server.fixture_secret.encode()))

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if path == '/healthz':
            try:
                uri = self.server.path.resolve().as_uri()+'?mode=ro'
                with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as db:
                    db.execute('SELECT id FROM jobs LIMIT 1').fetchone()
            except sqlite3.Error:
                return self.respond(503, {'status': 'unavailable'})
            return self.respond(200, {'status': 'ok'})
        if path in ASSETS:
            name, mime = ASSETS[path]
            return self.respond(200, (ROOT/'app'/'static'/name).read_bytes(), mime)
        if path.startswith('/runner-sim/'):
            if not self.local_fixture():
                return self.respond(403, {'error': 'Local mock browser required.'})
            try:
                key = path.split('/')[-1]
                with self.server.db(self.server.fixtures[key]) as db:
                    document = runner_fixtures.fixture_html(db, key, parse_qs(url.query).get('step',['contact'])[0])
                return self.respond(200, document, 'text/html')
            except (ValueError, KeyError):
                return self.respond(404, {'error': 'Synthetic fixture not found.'})
        if path not in ('/api/dashboard','/api/evidence') and not path.startswith(('/api/demo/job/','/api/demo/tasks/','/api/artifacts/')):
            return self.respond(404, {'error': 'Resource not found.'})
        try:
            with self.server.visitor(self.visitor_cookie(), path=='/api/dashboard') as (visitor, token), self.server.db(visitor.path) as db:
                self.new_visitor_token = token
                if path == '/api/dashboard':
                    return self.respond(200, summary(db))
                if path == '/api/evidence':
                    return self.respond(200, review.fact_view(db))
                if path.startswith('/api/demo/job/'):
                    job_id = int(path.split('/')[-1])
                    materials.job_record(db, job_id)
                    version = db.execute('SELECT id FROM material_versions WHERE job_id=? ORDER BY version DESC LIMIT 1',
                                         (job_id,)).fetchone()
                    task = db.execute("SELECT task_id FROM runner_runs WHERE job_id=? AND mode='simulation' ORDER BY task_id DESC LIMIT 1",
                                      (job_id,)).fetchone()
                    return self.respond(200, {'material': materials.version_record(db, version[0]) if version else None,
                                             'task': runner.view(db, task[0]) if task else None})
                if path.startswith('/api/demo/tasks/'):
                    task = demo_task(db, int(path.split('/')[-1]))
                    return self.respond(200, runner.view(db, task['task_id']))
                if path.startswith('/api/artifacts/'):
                    _, _, _, identifier, filename = path.split('/')
                    result = materials.artifact(db, int(identifier), filename)
                    if result:
                        mime = 'application/pdf' if filename.endswith('.pdf') else 'application/octet-stream'
                        return self.respond(200, result, mime)
        except SessionUnavailable as error:
            return self.respond(429 if self.visitor_cookie() or path=='/api/dashboard' else 404, {'error': str(error)})
        except (ValueError, KeyError):
            pass
        self.respond(404, {'error': 'Resource not found.'})

    def read_json(self):
        size = int(self.headers.get('Content-Length', '0'))
        origin = self.headers.get('Origin')
        if self.headers.get('Sec-Fetch-Site')=='cross-site' or (origin and urlsplit(origin).netloc!=self.headers.get('Host')):
            raise ValueError('Cross-origin request blocked.')
        if self.headers.get_content_type()!='application/json' or not 0<size<=8192:
            raise ValueError('Provide a small JSON request.')
        result = json.loads(self.rfile.read(size))
        if not isinstance(result, dict):
            raise ValueError('Provide a JSON object.')
        return result

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path == '/api/runner/mock-receipt':
                if not self.local_fixture():
                    return self.respond(403, {'error': 'Local mock browser required.'})
                key = self.read_json()['key']
                with self.server.db(self.server.fixtures[key]) as db:
                    receipt = runner_fixtures.simulation_receipt(db, key)
                return self.respond(200, receipt)
            if path not in {'/api/demo/reset','/api/demo/parse','/api/demo/generate','/api/demo/approve',
                            '/api/demo/tasks','/api/demo/answer','/api/demo/control'}:
                return self.respond(403, {'error': 'This operation is disabled in Synthetic Demo.'})
            payload = self.read_json()
            with self.server.visitor(self.visitor_cookie(), False) as (visitor, _):
                now = monotonic()
                self.server.prune(visitor.actions, now)
                if len(visitor.actions)>=40:
                    raise SessionUnavailable('Too many actions. Retry in a minute.')
                visitor.actions.append(now)
                if path == '/api/demo/reset':
                    if payload.get('confirmed') is not True:
                        raise ValueError('Confirm the reset of Synthetic Demo data.')
                    self.server.reset(visitor.path)
                    return self.respond(200, {'reset': True})
                with self.server.db(visitor.path) as db:
                    result = self.action(db, path, payload, visitor.path)
            return self.respond(200, result)
        except SessionUnavailable as error:
            return self.respond(429 if self.visitor_cookie() else 403, {'error': str(error)})
        except (ValueError, KeyError, TypeError):
            return self.respond(400, {'error': 'Invalid demo input or current workflow state.'})

    def action(self, db, path, payload, visitor_path):
        if path == '/api/demo/parse':
            return parse_demo(db, payload['job_id'])
        if path == '/api/demo/generate':
            if db.execute('SELECT COUNT(*) FROM material_versions').fetchone()[0]>=12:
                raise ValueError('Reset the demo before generating more versions.')
            keys = payload['fact_ids']
            if not isinstance(keys, list) or not 1<=len(keys)<=6 or any(type(key) is not int for key in keys):
                raise ValueError('Select verified facts.')
            jd = materials.jd_record(db, payload['jd_id'], payload['job_id'])
            role = jd['analysis']['role_suggestion'] or 'Application Developer'
            return materials.generate(db, {'job_id': payload['job_id'], 'jd_id': jd['id'], 'role': role,
                                'candidate': seed.CANDIDATE, 'bullets': [{'fact_id': key} for key in keys]})
        if path == '/api/demo/approve':
            if payload.get('confirmed') is not True:
                raise ValueError('Review required.')
            return materials.approve(db, {'version_id': payload['version_id']})
        if path == '/api/demo/tasks':
            if payload.get('mode','simulation') != 'simulation':
                raise ValueError('Real ATS is disabled.')
            job = db.execute('SELECT id,platform FROM jobs WHERE id=?', (payload['job_id'],)).fetchone()
            if not job or set(runner.approved_files(db, job['id'])) != {'resume','cover'}:
                raise ValueError('Approve materials before starting the mock ATS.')
            task = runner.create(db, {'job_id': job['id'], 'platform': job['platform'], 'mode': 'simulation'})
            if task['state']=='CREATED':
                db.execute('UPDATE runner_simulation SET signed_in=1 WHERE task_id=?', (task['task_id'],))
            return task
        row = demo_task(db, payload['task_id'])
        if path == '/api/demo/answer':
            fields = json.loads(row['checkpoint']).get('pending', [])
            field = next((field for field in fields if field['id']==payload['field_id']), None)
            if not field or payload['answer'] not in ('Full time','Part time','Institution website','Job fair','Other'):
                raise ValueError('Choose a predefined synthetic answer.')
            return runner.confirm_question(db, {'task_id': row['task_id'], 'field_id': field['id'],
                'scope': 'session', 'answer': payload['answer'], 'confirmed': payload.get('confirmed')})
        if not self.server.browser_slot.acquire(blocking=False):
            raise SessionUnavailable('The mock browser is busy. Retry shortly.')
        fixture_key = row['fixture_key']
        self.server.fixtures[fixture_key] = visitor_path
        try:
            result = runner.control(db, {key: payload[key] for key in ('task_id','action','confirmed','review_token')
                                         if key in payload}, self.server.fixture_base, self.server.fixture_secret)
        finally:
            del self.server.fixtures[fixture_key]
            self.server.browser_slot.release()
        if result['state']=='SUBMITTED':
            reconcile_receipts(db)
        return result


def main():
    path = ROOT/'runtime'/'portfolio-health.sqlite3'
    port = int(os.environ.get('PORT','8768'))
    if port in (8765,8766,8767):
        raise SystemExit('Choose a separate Portfolio port.')
    with DemoServer((os.environ.get('DEMO_BIND','127.0.0.1'), port), path) as server:
        print(f'Synthetic Demo listening on port {server.server_port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
