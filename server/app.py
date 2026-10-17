"""Internal recruitment service. Python 3.10+, standard library only.

All runtime paths are explicit, outside the public website and source checkout.
Run: python3 app.py --db /var/lib/recruitment/recruitment.sqlite3 serve
Create/reset an account locally: same --db, then user USERNAME --role ROLE.
"""
import argparse
import copy
import contextlib
import getpass
import hashlib
import hmac
import http.cookies
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DEPARTMENTS = ['权益部', '办公室', '活动部', '实践部', '宣媒部', '公关部']
ADMIN = '总负责人'
LOCK = threading.RLock()
STATE_KEYS = {'candidates', 'schedules', 'records', 'rankings', 'matches', 'departmentAvailability', 'interviewConfig', 'quotas', 'rules', 'timeline', 'notificationTemplates'}
MAX_BODY = 4 * 1024 * 1024
PUBLIC = Path(__file__).resolve().parent.parent / 'dist'


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def empty_state():
    return {
        'candidates': [], 'schedules': [], 'records': [], 'matches': [],
        'rankings': {d: [] for d in DEPARTMENTS},
        'departmentAvailability': {d: [] for d in DEPARTMENTS},
        'interviewConfig': {d: {'mode': 'single', 'batchSize': 6, 'singleDuration': 5, 'groupDuration': 20, 'gapMinutes': 0} for d in DEPARTMENTS},
        'quotas': dict(zip(DEPARTMENTS, [8, 8, 10, 9, 7, 6])),
        'rules': {'threePrefs': True, 'firstPrefFirst': True, 'uniqueAdmit': True, 'autoTransfer': False, 'lockAfterDeadline': True, 'preciseScore': False},
        'timeline': {'deadline': '', 'interviewEnd': ''}, 'notificationTemplates': {}
    }


@contextlib.contextmanager
def connect(db_path):
    db = sqlite3.connect(db_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, role TEXT NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at INTEGER NOT NULL, username TEXT NOT NULL, action TEXT NOT NULL, version INTEGER, detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts(key TEXT NOT NULL, at INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS attempts_key_time ON login_attempts(key,at);
        ''')
        db.execute('INSERT OR IGNORE INTO state VALUES (1, 0, ?)', (json.dumps(empty_state(), ensure_ascii=False),))
    if os.name != 'nt':
        os.chmod(db_path, 0o600)


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, maxmem=64 * 1024 * 1024).hex()


def set_user(db_path, username, role, password):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{3,40}', username) or role not in [ADMIN] + DEPARTMENTS:
        raise ValueError('用户名需为3–40位字母、数字、下划线或连字符；角色需为总负责人或正式部门名称。')
    if len(password) < 12 or len(password) > 128:
        raise ValueError('密码长度须为12–128位。')
    salt = secrets.token_hex(16)
    with connect(db_path) as db:
        db.execute('INSERT INTO users VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET role=excluded.role,salt=excluded.salt,password=excluded.password', (username, role, salt, password_hash(password, salt)))
        db.execute('DELETE FROM sessions WHERE username=?', (username,))
        db.execute('INSERT INTO audit(at,username,action,detail) VALUES(?,?,?,?)', (int(time.time()), username, 'account_reset', role))


def read_state(db):
    row = db.execute('SELECT version,payload FROM state WHERE id=1').fetchone()
    return row['version'], json.loads(row['payload'])


def scope(state, role):
    if role == ADMIN:
        return copy.deepcopy(state)
    value = copy.deepcopy(state)
    value['candidates'] = [c for c in state['candidates'] if role in c['prefs']]
    ids = {c['id'] for c in value['candidates']}
    # Cross-department times for shared candidates are needed to avoid clashes.
    value['schedules'] = [s for s in state['schedules'] if s['candidate'] in ids]
    value['records'] = [r for r in state['records'] if r['dept'] == role]
    value['rankings'] = {d: state['rankings'].get(d, []) if d == role else [] for d in DEPARTMENTS}
    value['matches'] = []
    return value


def validate_state(s):
    if not isinstance(s, dict) or set(s) != STATE_KEYS:
        raise APIError(400, '数据结构不正确，请刷新页面。')
    def safe(value, depth=0):
        if depth > 14:
            raise APIError(400, '数据嵌套过深。')
        if isinstance(value, str) and (len(value) > 20000 or '<' in value or '>' in value):
            raise APIError(400, '文本不能包含HTML标签或尖括号，且须在长度限制内。')
        if isinstance(value, dict):
            if any(k in ['__proto__', 'prototype', 'constructor'] for k in value):
                raise APIError(400, '数据含无效字段。')
            for v in value.values(): safe(v, depth + 1)
        elif isinstance(value, list):
            for v in value: safe(v, depth + 1)
    safe(s)
    for key in ['candidates', 'schedules', 'records', 'matches']:
        if not isinstance(s[key], list) or len(s[key]) > 20000:
            raise APIError(400, '记录数量或格式不正确。')
    ids = set()
    for c in s['candidates']:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', c['id']):
            raise APIError(400, '学号仅支持字母、数字、下划线和连字符。')
        prefs = c.get('prefs')
        if not isinstance(prefs, list) or not 1 <= len(prefs) <= 3 or len(set(prefs)) != len(prefs) or any(d not in DEPARTMENTS for d in prefs):
            raise APIError(400, '志愿需为1–3个不同的正式部门。')
        if c['id'] in ids or any(not isinstance(c.get(k), str) or not c[k].strip() for k in ['name', 'college']):
            raise APIError(400, '学号不得重复，姓名与学院不能为空。')
        ids.add(c['id'])
    candidates = {c['id']: c for c in s['candidates']}
    schedule_ids, record_keys = set(), set()
    for entry in s['schedules'] + s['records'] + s['matches']:
        if not isinstance(entry, dict) or entry.get('candidate') not in ids or entry.get('dept') not in DEPARTMENTS:
            raise APIError(400, '面试或录取记录引用了不存在的学生或部门。')
    for entry in s['schedules']:
        if entry['dept'] not in candidates[entry['candidate']]['prefs'] or not isinstance(entry.get('id'), int) or entry['id'] in schedule_ids:
            raise APIError(400, '面试编号重复或部门不在学生志愿中。')
        schedule_ids.add(entry['id'])
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(entry.get('date', ''))) or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(entry.get(k, ''))) for k in ['time', 'endTime']) or entry['endTime'] <= entry['time']:
            raise APIError(400, '面试时间须为同一天的有效起止时间。')
    for entry in s['records']:
        key = (entry['candidate'], entry['dept'])
        if key in record_keys or entry['dept'] not in candidates[entry['candidate']]['prefs'] or entry.get('decision') not in ['推荐', '候补观察', '不建议']:
            raise APIError(400, '面试评价重复或格式不正确。')
        record_keys.add(key)
        if not isinstance(entry.get('dims'), list) or len(entry['dims']) != 3 or any(v not in ['突出', '稳定', '待观察'] for v in entry['dims']):
            raise APIError(400, '面试评价需包含三项有效维度。')
    for key in ['rankings', 'departmentAvailability', 'interviewConfig', 'quotas']:
        if not isinstance(s[key], dict) or set(s[key]) != set(DEPARTMENTS):
            raise APIError(400, '部门配置不完整。')
    for dept in DEPARTMENTS:
        rank = s['rankings'][dept]
        if not isinstance(rank, list) or len(set(rank)) != len(rank) or any(i not in ids or dept not in candidates[i]['prefs'] for i in rank):
            raise APIError(400, '部门排序包含重复或无效学生。')
        quota = s['quotas'][dept]
        if not isinstance(quota, int) or not 0 <= quota <= 200:
            raise APIError(400, '部门名额须为0–200的整数。')
        cfg = s['interviewConfig'][dept]
        if not isinstance(cfg, dict) or cfg.get('mode') not in ['single', 'group']:
            raise APIError(400, '面试形式不正确。')
        for key, low, high in [('batchSize', 2, 50), ('singleDuration', 1, 240), ('groupDuration', 1, 240), ('gapMinutes', 0, 60)]:
            n = cfg.get(key)
            if not isinstance(n, int) or not low <= n <= high:
                raise APIError(400, '面试时长、间隔或人数不正确。')
        for slot in s['departmentAvailability'][dept]:
            if not isinstance(slot, dict) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(slot.get('date', ''))) or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(slot.get(k, ''))) for k in ['start', 'end']) or slot['end'] <= slot['start']:
                raise APIError(400, '部门时段格式不正确。')
    if not isinstance(s['rules'], dict) or any(not isinstance(v, bool) for v in s['rules'].values()):
        raise APIError(400, '招新规则格式不正确。')
    admitted = set()
    for match in s['matches']:
        if match['candidate'] in admitted or match.get('pref') not in [0, 1, 2, 3]:
            raise APIError(400, '同一学生不能重复录取。')
        admitted.add(match['candidate'])
    for dept in DEPARTMENTS:
        if sum(m['dept'] == dept for m in s['matches']) > s['quotas'][dept]:
            raise APIError(400, '录取人数超过部门名额。')


def merge_department(original, proposed, role):
    before = scope(original, role)
    for key in ['quotas', 'rules', 'timeline', 'notificationTemplates', 'matches']:
        if proposed[key] != before[key]:
            raise APIError(403, '本账号不能修改招新规则、通知模板或录取结果。')
    def without_status(items):
        return [{k: v for k, v in c.items() if k != 'status'} for c in items]
    if without_status(proposed['candidates']) != without_status(before['candidates']):
        raise APIError(403, '部门账号不能修改报名信息。')
    merged = copy.deepcopy(original)
    for key in ['schedules', 'records']:
        if [r for r in proposed[key] if r['dept'] != role] != [r for r in before[key] if r['dept'] != role]:
            raise APIError(403, '只能修改本部门的面试安排和评价。')
        merged[key] = [r for r in original[key] if r['dept'] != role] + [r for r in proposed[key] if r['dept'] == role]
    for key in ['rankings', 'departmentAvailability', 'interviewConfig']:
        for dept in DEPARTMENTS:
            if dept != role and proposed[key][dept] != before[key][dept]:
                raise APIError(403, '只能修改本部门的排序和面试设置。')
        merged[key][role] = proposed[key][role]
    return merged


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Recruitment'

        def log_message(self, *_):
            pass  # Never log request bodies, passwords, tokens or candidate records.

        def json_response(self, status, data, headers=None):
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for k, v in (headers or {}).items(): self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

        def body(self):
            if self.headers.get_content_type() != 'application/json':
                raise APIError(415, '需要JSON请求。')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY: raise APIError(413, '请求过大或为空。')
                value = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value, dict): raise ValueError()
                return value
            except (ValueError, UnicodeError):
                raise APIError(400, '请求格式不正确。')

        def secure(self):
            return self.client_address[0] in ['127.0.0.1', '::1'] and self.headers.get('X-Forwarded-Proto') == 'https'

        def require_transport(self):
            hostname = urlparse('http://' + self.headers.get('Host', '')).hostname
            if not self.secure() and hostname not in ['localhost', '127.0.0.1', '::1'] and os.environ.get('RECRUIT_ALLOW_HTTP') != '1':
                raise APIError(426, '请通过HTTPS登录；当前公网HTTP仅可查看登录页。')

        def session(self, db, mutation=False):
            cookies = http.cookies.SimpleCookie()
            try: cookies.load(self.headers.get('Cookie', ''))
            except http.cookies.CookieError: pass
            token = cookies.get('recruit_session')
            token_hash = hashlib.sha256(token.value.encode()).hexdigest() if token else ''
            row = db.execute('SELECT sessions.*,users.role FROM sessions JOIN users USING(username) WHERE token_hash=? AND expires>?', (token_hash, int(time.time()))).fetchone()
            if not row: raise APIError(401, '请登录，或重新登录已过期的账号。')
            if mutation and not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), row['csrf']):
                raise APIError(403, '请求验证失败，请刷新后重试。')
            return row

        def origin_check(self):
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                raise APIError(403, '不允许跨站提交。')

        def do_GET(self):
            try:
                route = urlparse(self.path).path
                if route == '/api/config': return self.json_response(200, {'mode': 'collaboration'})
                if route == '/api/health': return self.json_response(200, {'ok': True})
                if route.startswith('/api/'):
                    self.require_transport()
                    with connect(db_path) as db:
                        user = self.session(db)
                        if route == '/api/session':
                            return self.json_response(200, {'user': {'username': user['username'], 'role': user['role']}, 'csrf': user['csrf']})
                        if route == '/api/state':
                            version, value = read_state(db)
                            return self.json_response(200, {'version': version, 'state': scope(value, user['role'])})
                        if route == '/api/audit' and user['role'] == ADMIN:
                            rows = db.execute('SELECT at,username,action,version,detail FROM audit ORDER BY id DESC LIMIT 100').fetchall()
                            return self.json_response(200, {'entries': [dict(r) for r in rows]})
                    raise APIError(404, '接口不存在。')
                name = 'index.html' if route == '/' else route.lstrip('/')
                if name not in ['index.html', 'collaboration.js', 'xlsx.full.min.js', 'student-union-recruitment-template.xlsx']:
                    raise APIError(404, '文件不存在。')
                content = (PUBLIC / name).read_bytes()
                if name == 'index.html': content = content.replace(b'</head>', b'<script>window.RECRUIT_COLLAB=true;</script></head>')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8' if name.endswith('.html') else 'application/javascript; charset=utf-8' if name.endswith('.js') else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store' if name.endswith('.html') else 'no-cache')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'same-origin')
                self.send_header('X-Frame-Options', 'DENY')
                self.end_headers()
                self.wfile.write(content)
            except APIError as e: self.json_response(e.status, {'error': e.message})
            except (KeyError, TypeError, ValueError): self.json_response(400, {'error': '数据格式不正确。'})
            except Exception: self.json_response(500, {'error': '服务器处理失败，请联系总负责人。'})

        def do_POST(self):
            try:
                self.origin_check()
                self.require_transport()
                route = urlparse(self.path).path
                data = self.body()
                with LOCK, connect(db_path) as db:
                    if route == '/api/login':
                        now = int(time.time())
                        username, password = data.get('username', ''), data.get('password', '')
                        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 40 or len(password) > 128:
                            raise APIError(400, '用户名或密码格式不正确。')
                        # Persist attempts even when rejecting the request; reverse proxy peers cannot bypass throttling.
                        keys = ['user:' + username, 'peer:' + self.client_address[0]]
                        db.execute('DELETE FROM login_attempts WHERE at<?', (now - 900,))
                        if any(db.execute('SELECT COUNT(*) FROM login_attempts WHERE key=? AND at>?', (key, now - 900)).fetchone()[0] >= (10 if key.startswith('user:') else 60) for key in keys):
                            db.commit()
                            raise APIError(429, '登录尝试过多，请15分钟后再试。')
                        for key in keys: db.execute('INSERT INTO login_attempts VALUES(?,?)', (key, now))
                        db.commit()
                        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
                        salt = user['salt'] if user else '00' * 16
                        hashed = password_hash(password, salt)
                        if not user or not hmac.compare_digest(hashed, user['password']):
                            raise APIError(401, '用户名或密码不正确。')
                        db.execute('DELETE FROM login_attempts WHERE key=?', ('user:' + username,))
                        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                        db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
                        db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), username, csrf, now + 8 * 3600))
                        db.execute('INSERT INTO audit(at,username,action,detail) VALUES(?,?,?,?)', (now, username, 'login', ''))
                        cookie = 'recruit_session=' + token + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800' + ('; Secure' if self.secure() else '')
                        db.commit()
                        return self.json_response(200, {'user': {'username': username, 'role': user['role']}, 'csrf': csrf}, {'Set-Cookie': cookie})
                    user = self.session(db, mutation=True)
                    if route == '/api/logout':
                        db.execute('DELETE FROM sessions WHERE token_hash=?', (user['token_hash'],))
                        db.commit()
                        return self.json_response(200, {'ok': True}, {'Set-Cookie': 'recruit_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'})
                    if route != '/api/state': raise APIError(404, '接口不存在。')
                    db.execute('BEGIN IMMEDIATE')
                    version, original = read_state(db)
                    if data.get('version') != version:
                        raise APIError(409, '其他负责人已更新数据。本次修改未覆盖服务器，请先保留草稿，再刷新服务器数据。')
                    proposed = data.get('state')
                    validate_state(proposed)
                    merged = proposed if user['role'] == ADMIN else merge_department(original, proposed, user['role'])
                    validate_state(merged)
                    # Candidate progress is derived centrally, rather than trusted from a department's browser.
                    for candidate in merged['candidates']:
                        prefs = set(candidate['prefs'])
                        scheduled = {s['dept'] for s in merged['schedules'] if s['candidate'] == candidate['id'] and s['dept'] in prefs}
                        recorded = {r['dept'] for r in merged['records'] if r['candidate'] == candidate['id'] and r['dept'] in prefs}
                        candidate['status'] = '已完成' if prefs <= recorded else '已安排' if prefs <= scheduled else '待处理' if scheduled else '待安排'
                    db.execute('UPDATE state SET version=?,payload=? WHERE id=1', (version + 1, json.dumps(merged, ensure_ascii=False, allow_nan=False)))
                    counts = json.dumps({k: len(merged[k]) for k in ['candidates', 'schedules', 'records', 'matches']}, ensure_ascii=False)
                    db.execute('INSERT INTO audit(at,username,action,version,detail) VALUES(?,?,?,?,?)', (int(time.time()), user['username'], 'save', version + 1, counts))
                    db.commit()
                    return self.json_response(200, {'version': version + 1})
            except APIError as e: self.json_response(e.status, {'error': e.message})
            except (KeyError, TypeError, ValueError): self.json_response(400, {'error': '数据格式不正确。'})
            except Exception: self.json_response(500, {'error': '服务器处理失败，本次修改未保存。'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='数据库绝对路径，必须在网站目录之外')
    commands = parser.add_subparsers(dest='command', required=True)
    serving = commands.add_parser('serve')
    serving.add_argument('--port', type=int, default=8787)
    user = commands.add_parser('user')
    user.add_argument('username')
    user.add_argument('--role', required=True, choices=[ADMIN] + DEPARTMENTS)
    backup = commands.add_parser('backup')
    backup.add_argument('output')
    args = parser.parse_args()
    db_path = str(Path(args.db).resolve())
    if not Path(args.db).is_absolute() or PUBLIC in Path(db_path).parents:
        parser.error('数据库必须使用网站目录之外的绝对路径。')
    init_db(db_path)
    if args.command == 'user':
        password = getpass.getpass('输入新密码（至少12位，不显示）：')
        if password != getpass.getpass('再次输入密码：'): raise SystemExit('两次密码不一致，未修改账号。')
        set_user(db_path, args.username, args.role, password)
        print('账号已创建或重置，旧会话已退出。')
    elif args.command == 'backup':
        output = Path(args.output).resolve()
        if PUBLIC in output.parents: raise SystemExit('备份不能放在公开网站目录。')
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists(): raise SystemExit('备份目标已存在，请使用新文件名。')
        with connect(db_path) as source, contextlib.closing(sqlite3.connect(str(output))) as target: source.backup(target)
        if os.name != 'nt': os.chmod(output, 0o600)
        print('数据库备份完成。')
    else:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(db_path))
        print(f'Recruitment service: http://127.0.0.1:{args.port}', flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: server.server_close()


if __name__ == '__main__': main()
