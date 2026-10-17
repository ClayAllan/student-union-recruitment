"""Run with python -B; RECRUIT_TEST_DIR must point into the task workspace."""
import copy
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import app


class Client:
    def __init__(self, url):
        self.url = url
        self.csrf = ''
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, data=None, csrf=True, headers=None):
        extra = {'Content-Type': 'application/json'} if data is not None else {}
        if csrf: extra['X-CSRF-Token'] = self.csrf
        extra.update(headers or {})
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=extra)
        try:
            with self.opener.open(req, timeout=10) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as error: return error.code, json.load(error)

    def login(self, username, password):
        status, result = self.request('/api/login', {'username': username, 'password': password})
        if status == 200: self.csrf = result['csrf']
        return status, result


class CollaborationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['RECRUIT_TEST_DIR'])
        self.db_path = str(Path(self.tmp.name) / 'test.sqlite3')
        app.init_db(self.db_path)
        self.password = secrets.token_urlsafe(20)
        for name, role in [('manager', app.ADMIN), ('rights', '权益部'), ('office', '办公室')]:
            app.set_user(self.db_path, name, role, self.password)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.make_handler(self.db_path))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.manager = Client(self.url)
        self.assertEqual(self.manager.login('manager', self.password)[0], 200)
        _, initial = self.manager.request('/api/state')
        state = initial['state']
        for ident, prefs in [('QA1', ['权益部', '办公室']), ('QA2', ['办公室'])]:
            state['candidates'].append({'id': ident, 'name': '虚构测试', 'college': '测试学院', 'prefs': prefs, 'availability': [], 'slots': [], 'contact': '', 'note': '', 'status': '待安排'})
            for dept in prefs: state['rankings'][dept].append(ident)
        self.assertEqual(self.manager.request('/api/state', {'version': initial['version'], 'state': state})[0], 200)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def client(self, username):
        client = Client(self.url)
        self.assertEqual(client.login(username, self.password)[0], 200)
        return client

    def test_authentication_scope_and_password_storage(self):
        anonymous = Client(self.url)
        self.assertEqual(anonymous.request('/api/state')[0], 401)
        rights = self.client('rights')
        _, result = rights.request('/api/state')
        self.assertEqual([c['id'] for c in result['state']['candidates']], ['QA1'])
        self.assertEqual(result['state']['rankings']['办公室'], [])
        self.assertEqual(rights.request('/api/audit')[0], 404)
        with app.connect(self.db_path) as db:
            stored = db.execute('SELECT password FROM users WHERE username=?', ('rights',)).fetchone()[0]
            self.assertNotEqual(stored, self.password)
            self.assertNotIn(self.password, Path(self.db_path).read_bytes().decode('latin1'))

    def test_department_cannot_change_other_departments_or_applicants(self):
        rights = self.client('rights')
        _, baseline = rights.request('/api/state')
        forbidden = []
        s = copy.deepcopy(baseline); s['state']['quotas']['权益部'] += 1; forbidden.append(s)
        s = copy.deepcopy(baseline); s['state']['candidates'][0]['name'] = '篡改'; forbidden.append(s)
        s = copy.deepcopy(baseline); s['state']['interviewConfig']['办公室']['groupDuration'] = 30; forbidden.append(s)
        s = copy.deepcopy(baseline); s['state']['schedules'].append({'id': 100, 'candidate': 'QA1', 'dept': '办公室', 'date': '2030-10-09', 'time': '19:00', 'endTime': '19:05', 'room': '测试'}); forbidden.append(s)
        for payload in forbidden:
            self.assertEqual(rights.request('/api/state', payload)[0], 403)
        self.assertEqual(self.manager.request('/api/state')[1]['version'], baseline['version'])

    def test_shared_save_and_stale_version_conflict(self):
        rights, office = self.client('rights'), self.client('office')
        _, r = rights.request('/api/state')
        _, stale = office.request('/api/state')
        r['state']['schedules'].append({'id': 101, 'candidate': 'QA1', 'dept': '权益部', 'date': '2030-10-09', 'time': '19:00', 'endTime': '19:05', 'room': '测试'})
        self.assertEqual(rights.request('/api/state', r)[0], 200)
        self.assertEqual(office.request('/api/state', stale)[0], 409)
        _, latest = office.request('/api/state')
        self.assertEqual(latest['state']['schedules'][0]['dept'], '权益部')
        latest['state']['schedules'].append({'id': 102, 'candidate': 'QA1', 'dept': '办公室', 'date': '2030-10-09', 'time': '19:30', 'endTime': '19:35', 'room': '测试'})
        self.assertEqual(office.request('/api/state', latest)[0], 200)
        self.assertEqual(len(self.manager.request('/api/state')[1]['state']['schedules']), 2)

    def test_csrf_origin_logout_and_reset_revoke_sessions(self):
        rights = self.client('rights')
        _, payload = rights.request('/api/state')
        self.assertEqual(rights.request('/api/state', payload, csrf=False)[0], 403)
        self.assertEqual(rights.request('/api/state', payload, headers={'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(rights.request('/api/logout', {})[0], 200)
        self.assertEqual(rights.request('/api/state')[0], 401)
        rights = self.client('rights')
        app.set_user(self.db_path, 'rights', '权益部', secrets.token_urlsafe(20))
        self.assertEqual(rights.request('/api/state')[0], 401)

    def test_persistence_audit_and_backup(self):
        _, payload = self.manager.request('/api/state')
        self.assertEqual(self.manager.request('/api/state', payload)[0], 200)
        with app.connect(self.db_path) as db:
            _, state = app.read_state(db)
            self.assertEqual(len(state['candidates']), 2)
            target = str(Path(self.tmp.name) / 'backup.sqlite3')
            with app.contextlib.closing(app.sqlite3.connect(target)) as dest: db.backup(dest)
        with app.connect(target) as restored:
            self.assertEqual(app.read_state(restored)[1], state)
        entries = self.manager.request('/api/audit')[1]['entries']
        self.assertTrue(any(r['action'] == 'save' for r in entries))
        self.assertTrue(all('虚构测试' not in r['detail'] for r in entries))

    def test_input_validation_throttling_and_public_http(self):
        _, payload = self.manager.request('/api/state')
        payload['state']['candidates'][0]['name'] = '<img src=x onerror=alert(1)>'
        self.assertEqual(self.manager.request('/api/state', payload)[0], 400)
        self.assertEqual(self.manager.request('/api/state', {}, headers={'Host': 'public.example'})[0], 426)
        anonymous = Client(self.url)
        for _ in range(10): self.assertEqual(anonymous.login('missing', 'incorrect password')[0], 401)
        self.assertEqual(anonymous.login('missing', 'incorrect password')[0], 429)


if __name__ == '__main__': unittest.main(verbosity=2)
