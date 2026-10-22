"""Classic interface collaboration. All authorization is enforced server-side."""
import contextlib
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from errors import APIError, require
from classic import validate_state

VERSION = '20261021-classic-collaboration-v2'
APPLICATION = 'classic-collaboration'
SCHEMA = '2'
DEPARTMENTS = ['办公室', '公关部', '活动部', '权益部', '实践部', '宣媒部']
# Display sorting must never change the historic transfer processing priority.
MATCH_ORDER = ['权益部', '办公室', '活动部', '实践部', '宣媒部', '公关部']
ROLES = {'admin': '总负责人', 'lead': '主要负责人', 'head': '负责人', 'staff': '工作人员'}
QUOTAS = {'权益部': 8, '办公室': 8, '活动部': 10, '实践部': 9, '宣媒部': 7, '公关部': 6}
ROOMS = {'权益部': '203', '办公室': '209', '活动部': '205', '实践部': '201', '宣媒部': '207', '公关部': '211'}
STATE_KEYS = {'candidates', 'schedules', 'records', 'matches', 'rankings', 'departmentAvailability', 'interviewConfig', 'quotas', 'rules', 'timeline', 'notificationTemplates'}
CONFIG = {'mode': 'single', 'batchSize': 6, 'singleDuration': 5, 'groupDuration': 20, 'gapMinutes': 0}
now = lambda: int(time.time())
dumps = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def text(value, maximum=2000, minimum=0):
    require(isinstance(value, str) and minimum <= len(value) <= maximum and '\x00' not in value and '<' not in value and '>' not in value,
            '文字格式或长度不正确，不能包含尖括号。', 400)
    return value


def unique(values, limit=500):
    require(isinstance(values, list) and len(values) <= limit and all(isinstance(x, str) for x in values), '列表格式不正确。', 400)
    require(len(values) == len(set(values)), '列表不能重复。', 400)
    return values


def password_check(value, username=''):
    require(isinstance(value, str) and 8 <= len(value) <= 128 and '\x00' not in value, '密码须为8—128字符。', 400)
    require(value.casefold() not in {'12345678', '123456789', 'password', 'password123', 'qwerty123', 'abcdefgh', username.casefold()} and len(set(value)) >= 3,
            '密码过于常见，请换一个独特的密码。', 400)
    return value


def password_hash(value, salt):
    return hashlib.scrypt(value.encode('utf-8'), salt=bytes.fromhex(salt), n=16384, r=8, p=1, maxmem=64*1024*1024).hex()


def verify(user, value):
    return isinstance(value, str) and len(value) <= 128 and hmac.compare_digest(password_hash(value, user['salt'] if user else '00'*16), user['password'] if user else '00'*64) and bool(user)


def empty_state():
    return {'candidates': [], 'schedules': [], 'records': [], 'matches': [],
            'rankings': {d: [] for d in DEPARTMENTS}, 'departmentAvailability': {d: [] for d in DEPARTMENTS},
            'interviewConfig': {d: copy.deepcopy(CONFIG) for d in DEPARTMENTS}, 'quotas': dict(QUOTAS),
            'rules': {'threePrefs': True, 'firstPrefFirst': True, 'uniqueAdmit': True, 'autoTransfer': False, 'lockAfterDeadline': True, 'preciseScore': False},
            'timeline': {'deadline': '', 'interviewEnd': ''}, 'notificationTemplates': {}}


@contextlib.contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Inspect before any journal/schema write. A foreign DB must stay byte-identical.
    with connect(path) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables:
            require('metadata' in tables, '数据库属于其他版本；请使用独立新库或副本迁移工具。', 400)
            metadata = dict(db.execute('SELECT key,value FROM metadata'))
            require(metadata.get('application') == APPLICATION and metadata.get('schema') == SCHEMA, '数据库应用或结构版本不兼容。', 400)
            return
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE users(username TEXT PRIMARY KEY,display_name TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','lead','head','staff')),
          department TEXT NOT NULL,salt TEXT NOT NULL,password TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,must_change INTEGER NOT NULL DEFAULT 0,
          temp_until INTEGER NOT NULL DEFAULT 0,can_interview INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL,last_login INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL REFERENCES users(username),csrf TEXT NOT NULL,expires INTEGER NOT NULL,verified INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,agent TEXT NOT NULL DEFAULT '');
        CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE tasks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,department TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(username),version INTEGER NOT NULL,data TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL);
        CREATE TABLE reviews(id TEXT PRIMARY KEY,candidate TEXT NOT NULL,department TEXT NOT NULL,author TEXT NOT NULL REFERENCES users(username),task TEXT NOT NULL REFERENCES tasks(id),version INTEGER NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(candidate,department,author));
        CREATE TABLE events(id INTEGER PRIMARY KEY,task TEXT NOT NULL REFERENCES tasks(id),actor TEXT NOT NULL,at INTEGER NOT NULL,action TEXT NOT NULL,note TEXT NOT NULL);
        CREATE TABLE audit(id INTEGER PRIMARY KEY,at INTEGER NOT NULL,username TEXT NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL);
        CREATE TABLE login_attempts(key TEXT NOT NULL,at INTEGER NOT NULL);
        CREATE INDEX attempts_key_time ON login_attempts(key,at);
        CREATE TABLE requests(username TEXT NOT NULL,request_id TEXT NOT NULL,body_hash TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(username,request_id));
        ''')
        db.executemany('INSERT INTO metadata VALUES(?,?)', [('application', APPLICATION), ('schema', SCHEMA), ('auth_revision', '1')])
        db.execute('INSERT INTO state VALUES(1,0,?)', (dumps(empty_state()),))
    if os.name != 'nt':
        os.chmod(path, 0o600)


def audit(db, user, action, detail=''):
    db.execute('INSERT INTO audit(at,username,action,detail) VALUES(?,?,?,?)', (now(), user, action, detail))


def bump(db):
    db.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='auth_revision'")


def public_user(user):
    fields = ('username', 'display_name', 'role', 'department', 'active', 'must_change', 'can_interview', 'revision', 'last_login')
    return {**{k: user[k] for k in fields}, 'role_label': ROLES[user['role']]}


def validate_identity(role, dept):
    require(role in ROLES and (role == 'admin' and dept == '' or role != 'admin' and dept in DEPARTMENTS), '请选择有效岗位和所属部门；总负责人部门应为空。', 400)


def set_user(path, username, role, password, department='', display_name='', can_interview=False):
    """Trusted local create/reset. Does not activate unresolved migrated shared accounts."""
    require(re.fullmatch('[A-Za-z0-9_-]{3,40}', username) is not None, '账号须为3—40位字母、数字、下划线或连字符。', 400)
    validate_identity(role, department)
    password_check(password, username)
    salt = secrets.token_hex(16)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if old:
            protect_last_admin(db, old, role, True)
            db.execute('UPDATE users SET display_name=?,role=?,department=?,salt=?,password=?,active=1,must_change=0,temp_until=0,can_interview=?,revision=revision+1 WHERE username=?',
                       (text(display_name or username, 80, 1), role, department, salt, password_hash(password, salt), int(can_interview), username))
        else:
            db.execute('INSERT INTO users(username,display_name,role,department,salt,password,can_interview,created) VALUES(?,?,?,?,?,?,?,?)',
                       (username, text(display_name or username, 80, 1), role, department, salt, password_hash(password, salt), int(can_interview), now()))
        db.execute('DELETE FROM sessions WHERE username=?', (username,))
        bump(db)
        audit(db, username, 'local_account_reset')


def protect_last_admin(db, target, role, active):
    if target['role'] == 'admin' and target['active'] and (role != 'admin' or not active):
        require(db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND username<>?", (target['username'],)).fetchone()[0] > 0, '不能停用或降级最后一个总负责人。', 409)


def read_state(db):
    row = db.execute('SELECT version,payload FROM state WHERE id=1').fetchone()
    return row['version'], json.loads(row['payload'])


def task_row(row):
    return {**{k: row[k] for k in ('id', 'kind', 'department', 'created_by', 'version', 'created', 'updated')}, 'data': json.loads(row['data'])}


def all_tasks(db):
    return {r['id']: task_row(r) for r in db.execute('SELECT * FROM tasks ORDER BY created DESC,id')}


def active_task(db, task, tasks=None, seen=None):
    tasks, seen = tasks if tasks is not None else all_tasks(db), set() if seen is None else seen
    d = task['data']
    if task['id'] in seen or len(seen) > 20 or d['status'] in ('已完成', '已取消') or d['access_until'] <= now() or d.get('stale'):
        return False
    seen.add(task['id'])
    creator = db.execute('SELECT * FROM users WHERE username=?', (task['created_by'],)).fetchone()
    if not creator or not creator['active'] or creator['must_change'] or creator['role'] == 'staff':
        return bool(d.get('private') and creator and creator['active'] and not creator['must_change'])
    if creator['role'] != 'admin' and creator['department'] != task['department']:
        return False
    parent_id = d.get('parent')
    if parent_id:
        parent = tasks.get(parent_id)
        if not parent or not active_task(db, parent, tasks, seen): return False
        pd = parent['data']
        return (task['created_by'] in pd['members'] and parent['kind'] == task['kind'] and parent['department'] == task['department']
                and set(d['candidates']) <= set(pd['candidates']) and set(d['schedule_ids']) <= set(pd['schedule_ids'])
                and set(d['fields']) <= set(pd['fields']) and d['access_until'] <= pd['access_until']
                and (not d.get('allow_schedule') or pd.get('allow_schedule')) and (not d.get('allow_settings') or pd.get('allow_settings')))
    return creator['role'] in ('admin', 'lead') or task['kind'] == 'general' and creator['role'] == 'head'


class Policy:
    def __init__(self, db, user, state=None):
        self.db, self.user = db, user
        self.state = read_state(db)[1] if state is None else state
        self.admin = user['role'] == 'admin'
        self.lead = user['role'] == 'lead'
        self.dept = user['department']
        self.tasks = all_tasks(db)
        self.mine = [t for t in self.tasks.values() if user['username'] in t['data']['members'] and t['department'] == self.dept and active_task(db, t, self.tasks)]

    def manage(self, dept):
        return self.admin or self.lead and dept == self.dept

    def visible(self, c):
        return self.admin or self.dept in c['prefs'] and (self.lead or any(c['id'] in t['data']['candidates'] for t in self.mine))

    def schedule(self, c):
        return self.manage(self.dept) or self.user['role'] == 'head' and any(t['kind'] == 'interview' and t['data'].get('allow_schedule') and c in t['data']['candidates'] for t in self.mine)

    def settings(self):
        return self.admin or self.lead or self.user['role'] == 'head' and any(t['data'].get('allow_settings') for t in self.mine)

    def task_manage(self, task):
        if task['data'].get('private'): return task['created_by'] == self.user['username']
        return self.manage(task['department']) or self.user['role'] == 'head' and task['created_by'] == self.user['username'] and task['department'] == self.dept

    def task_visible(self, task):
        if task['data'].get('private'): return task['created_by'] == self.user['username']
        return self.manage(task['department']) or task['department'] == self.dept and (task['created_by'] == self.user['username'] or self.user['username'] in task['data']['members'])

    def review_allowed(self, task, candidate):
        c = next((c for c in self.state['candidates'] if c['id'] == candidate), None)
        return (c is not None and task['kind'] == 'interview' and task['department'] in c['prefs'] and candidate in task['data']['candidates']
                and self.user['username'] in task['data']['reviewers'] and bool(self.user['can_interview'])
                and (self.admin or self.dept == task['department']) and active_task(self.db, task, self.tasks))

    def project(self):
        value = copy.deepcopy(self.state)
        candidates = [c for c in self.state['candidates'] if self.visible(c)]
        ids = {c['id'] for c in candidates}
        if not self.admin:
            projected = []
            for c in candidates:
                item = {k: copy.deepcopy(c[k]) for k in ('id','name','college','availability','slots','free','status') if k in c}
                item.update(prefs=copy.deepcopy(c['prefs']) if self.lead else [self.dept], contact='', note='')
                projected.append(item)
            value['candidates'] = projected
            # Other-department occupancy contains only the minimum time fields.
            value['schedules'] = [{k: s[k] for k in ('id','candidate','dept','date','time','endTime') if k in s} | ({'room': s.get('room','')} if s['dept'] == self.dept else {'room': '其他部门已占用'}) for s in value['schedules'] if s['candidate'] in ids]
            value['records'] = [r for r in value['records'] if r['dept'] == self.dept and r['candidate'] in ids] if self.lead else []
            value['rankings'] = {d: value['rankings'][d] if d == self.dept and self.lead else [] for d in DEPARTMENTS}
            value['departmentAvailability'] = {d: value['departmentAvailability'][d] if d == self.dept and self.settings() else [] for d in DEPARTMENTS}
            value['interviewConfig'] = {d: value['interviewConfig'][d] if d == self.dept else copy.deepcopy(CONFIG) for d in DEPARTMENTS}
            value['matches'] = []
        return value


def scope_revision(db, user):
    """Changes on time-based expiry as well as explicit revocation; no polling renewal."""
    p = Policy(db, user)
    signature = [user['username'],user['role'],user['department'],user['can_interview'],user['revision'],
                 sorted(c['id'] for c in p.state['candidates'] if p.visible(c)),
                 sorted((t['id'],dumps({k:t['data'][k] for k in ('candidates','schedule_ids','fields','reviewers','allow_schedule','allow_settings','access_until')})) for t in p.mine)]
    return hashlib.sha256(dumps(signature).encode()).hexdigest()


def bootstrap(db, user):
    p = Policy(db, user)
    version, _ = read_state(db)
    revision = db.execute("SELECT value FROM metadata WHERE key='auth_revision'").fetchone()[0]
    return {'user': public_user(user), 'version': version, 'state': p.project(), 'revision': revision, 'scope_revision': scope_revision(db,user),
            'departments': DEPARTMENTS, 'roles': ROLES,
            'permissions': {'admin': p.admin, 'manage': p.admin or p.lead, 'settings': p.settings(),
                            'schedule': p.admin or p.lead or user['role'] == 'head' and any(t['data'].get('allow_schedule') for t in p.mine)}}


def recent(session):
    require(session['verified'] >= now() - 300, '此操作需要再次验证本人密码。', 428)


def account_action(db, actor, session, action, data):
    require(actor['role'] == 'admin')
    recent(session)
    username = text(data.get('username'), 40, 3)
    if action == 'create':
        require(re.fullmatch('[A-Za-z0-9_-]{3,40}', username) is not None, '账号格式不正确。', 400)
        require(not db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone(), '账号已存在。', 409)
        role, dept = data.get('role'), data.get('department','')
        validate_identity(role, dept)
        initial, salt = secrets.token_urlsafe(12), secrets.token_hex(16)
        db.execute('INSERT INTO users(username,display_name,role,department,salt,password,must_change,temp_until,can_interview,created) VALUES(?,?,?,?,?,?,1,?,?,?)',
                   (username,text(data.get('display_name'),80,1),role,dept,salt,password_hash(initial,salt),now()+86400,int(bool(data.get('can_interview'))),now()))
        audit(db,actor['username'],'user_create',username)
        bump(db)
        return {'temporary_password': initial}
    target = db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
    require(target is not None, '账号不存在。', 404)
    require(data.get('revision') == target['revision'], '账号已被更新，请刷新。', 409)
    if action == 'edit':
        role, dept, active = data.get('role'), data.get('department',''), bool(data.get('active'))
        validate_identity(role,dept)
        protect_last_admin(db,target,role,active)
        db.execute('UPDATE users SET display_name=?,role=?,department=?,active=?,can_interview=?,revision=revision+1 WHERE username=?',
                   (text(data.get('display_name'),80,1),role,dept,int(active),int(bool(data.get('can_interview'))),username))
    elif action == 'reset':
        require(target['active'], '停用账号请先核实并启用，再重置密码。', 409)
        initial, salt = secrets.token_urlsafe(12), secrets.token_hex(16)
        db.execute('UPDATE users SET salt=?,password=?,must_change=1,temp_until=?,revision=revision+1 WHERE username=?',(salt,password_hash(initial,salt),now()+86400,username))
    elif action == 'revoke':
        db.execute('UPDATE users SET revision=revision+1 WHERE username=?',(username,))
    else:
        raise APIError(404,'账号操作不存在。')
    db.execute('DELETE FROM sessions WHERE username=?',(username,))
    audit(db,actor['username'],'user_'+action,username)
    bump(db)
    return {'ok':True, **({'temporary_password': initial} if action == 'reset' else {})}


def member_list(db, user):
    rows = db.execute('SELECT * FROM users WHERE active=1 AND must_change=0').fetchall()
    if user['role'] != 'admin': rows = [r for r in rows if r['department'] == user['department']]
    if user['role'] == 'staff': rows = [r for r in rows if r['username'] == user['username']]
    keys = ('username','display_name','role','department','can_interview')
    return sorted([{k:r[k] for k in keys} for r in rows], key=lambda u:(list(ROLES).index(u['role']),DEPARTMENTS.index(u['department']) if u['department'] else -1,u['display_name'],u['username']))


def schedule_fingerprint(schedules, ids):
    return hashlib.sha256(dumps(sorted([s for s in schedules if str(s['id']) in ids],key=lambda s:s['id'])).encode()).hexdigest()


def task_public(db, user, task, p=None):
    p = p or Policy(db,user)
    require(p.task_visible(task))
    item = copy.deepcopy(task)
    d = item['data']
    effective = active_task(db,task,p.tasks)
    if not effective:
        d['candidates'], d['schedule_ids'], d['progress'], d['template'] = [], [], {}, ''
    item['effective'] = effective
    item['can_manage'] = p.task_manage(task)
    item['can_execute'] = effective and user['username'] in d['members']
    item['can_delegate'] = effective and user['role'] == 'head' and user['username'] in d['members']
    item['overdue'] = d['due'] > 0 and d['due'] < now() and d['status'] not in ('已完成','已取消')
    item['events'] = [dict(r) for r in db.execute('SELECT actor,at,action,note FROM events WHERE task=? ORDER BY id DESC LIMIT 30',(task['id'],))]
    if not effective: item['events'] = [{**r,'note':''} for r in item['events']]
    return item


def save_task(db,user,data):
    p = Policy(db,user)
    old = p.tasks.get(data.get('id'))
    if old:
        require(p.task_manage(old))
        require(data.get('version') == old['version'],'任务已更新，请重新打开。',409)
        require(old['data']['status'] not in ('已完成','已取消'), '已关闭任务不能编辑，请新建任务。',409)
    kind = data.get('kind')
    require(kind in ('interview','notice','general'),'任务类型无效。',400)
    dept = data.get('department')
    require(dept in DEPARTMENTS, '请选择部门。',400)
    if old: require(kind == old['kind'] and dept == old['department'], '不能修改已有任务类型或部门。',400)
    private = bool(data.get('private'))
    require(not private or kind == 'general','只有通用待办可设为私人。',400)
    members = unique(data.get('members',[]),100)
    require(members,'请选择执行人。',400)
    assignee = text(data.get('assignee'),40,3)
    require(assignee in members,'主执行人必须在任务成员中。',400)
    require(user['role'] in ('admin','lead','head') or private and members == [user['username']])
    require(p.admin or dept == p.dept)
    if private: require(members == [user['username']],'私人待办只能分配本人。',400)
    for name in members:
        member = db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
        require(member and member['active'] and not member['must_change'] and (member['department'] == dept or member['role']=='admin'),'只能分配本部门已启用且已改密人员。',400)
        if not p.admin and not private:
            require(member['role'] in (('head','staff') if p.lead else ('staff',)), '不能向同级或上级分配。')
    candidates = unique(data.get('candidates',[]))
    schedule_ids = unique(data.get('schedule_ids',[]))
    fields = unique(data.get('fields',[]),1)
    require(set(fields) <= {'contact'},'只支持原报名表的联系方式字段。',400)
    require(kind == 'notice' or not fields,'此任务不能授予联系方式。',400)
    require(kind != 'notice' or candidates and fields,'通知任务需选择学生和联系方式字段。',400)
    require(kind != 'interview' or candidates and schedule_ids,'面试任务需关联已排期学生。',400)
    require(kind != 'general' or not candidates and not schedule_ids and not fields,'通用待办不关联学生和联系方式。',400)
    for cid in candidates:
        c = next((c for c in p.state['candidates'] if c['id']==cid),None)
        require(c is not None and dept in c['prefs'] and (p.admin or p.visible(c)), '学生不属于可分配范围。')
    if kind == 'interview':
        chosen = [s for s in p.state['schedules'] if str(s['id']) in schedule_ids]
        require(len(chosen)==len(schedule_ids) and all(s['dept']==dept and s['candidate'] in candidates for s in chosen) and {s['candidate'] for s in chosen}==set(candidates),'场次必须完整对应所选学生和部门。',400)
    elif schedule_ids: raise APIError(400,'此任务不能附带面试场次授权。')
    reviewers = unique(data.get('reviewers',[]),100)
    require(kind == 'interview' or not reviewers,'只有面试任务允许评价人员。',400)
    if kind == 'interview':
        require(reviewers and set(reviewers)<=set(members),'请选择任务成员中的面试人员。',400)
        for name in reviewers: require(db.execute('SELECT can_interview FROM users WHERE username=?',(name,)).fetchone()[0], '选中人员尚无面试资格。',400)
    due, until = data.get('due',0), data.get('access_until')
    require(type(due) is int and due>=0 and type(until) is int and now()<until<=now()+60*86400,'期限须在未来60天内。',400)
    parent_id = text(data.get('parent',''),64)
    parent = p.tasks.get(parent_id) if parent_id else None
    allow_schedule, allow_settings = bool(data.get('allow_schedule')), bool(data.get('allow_settings'))
    require(kind=='interview' or not (allow_schedule or allow_settings),'排期授权只能附在面试任务。',400)
    if not p.manage(dept) and kind != 'general':
        require(user['role']=='head' and parent and active_task(db,parent,p.tasks) and user['username'] in parent['data']['members'], '负责人必须选择有效的上级任务。')
    if parent:
        pd=parent['data']
        require(not old or old['id'] != parent_id,'任务不能作为自己的上级。',400)
        require(parent['kind']==kind and parent['department']==dept and active_task(db,parent,p.tasks) and user['username'] in pd['members'],'上级任务无效。')
        require(set(candidates)<=set(pd['candidates']) and set(schedule_ids)<=set(pd['schedule_ids']) and set(fields)<=set(pd['fields']) and until<=pd['access_until'],'再分配不能扩大上级范围或期限。')
        require(not allow_schedule or pd.get('allow_schedule'))
        require(not allow_settings or pd.get('allow_settings'))
        seen={old['id']} if old else set()
        check=parent
        while check:
            require(check['id'] not in seen and len(seen)<20,'上级任务关系不能循环。',400)
            seen.add(check['id']); check=p.tasks.get(check['data'].get('parent'))
    elif not p.manage(dept): require(kind=='general')
    previous = old['data'] if old else {}
    # A principal reassignment retains contact history, not hidden old authorization.
    progress={cid:previous.get('progress',{}).get(cid,{'status':'待联系','note':''}) for cid in candidates} if kind=='notice' else {}
    if kind=='notice':
        for other in p.tasks.values():
            if old and other['id']==old['id'] or parent and other['id']==parent_id: continue
            od=other['data']
            if other['kind']=='notice' and other['department']==dept and active_task(db,other,p.tasks) and od.get('parent','')==parent_id and set(od['candidates']) & set(candidates):
                require(od.get('template')!=data.get('template'), '同一通知事项已有有效任务，请改派原任务，避免重复联系。',409)
    payload={'title':text(data.get('title'),120,1),'description':text(data.get('description',''),4000),'members':members,'assignee':assignee,
             'reviewers':reviewers,'candidates':candidates,'schedule_ids':schedule_ids,'fields':fields,'due':due,'access_until':until,'parent':parent_id,
             'allow_schedule':allow_schedule,'allow_settings':allow_settings,'private':private,'priority':data.get('priority','普通'),
             'status':previous.get('status','待开始'),'template':text(data.get('template',''),2000),'progress':progress,'stale':False,
             'fingerprint':schedule_fingerprint(p.state['schedules'],schedule_ids),'checklist':text(data.get('checklist',''),4000)}
    require(payload['priority'] in ('普通','优先','紧急'),'优先级无效。',400)
    if old:
        # Even an administrator must explicitly reassign, not silently widen a child task.
        if old['created_by'] != user['username'] and user['role']=='head': raise APIError(403,'不能编辑上级任务。')
        db.execute('UPDATE tasks SET version=version+1,data=?,updated=? WHERE id=?',(dumps(payload),now(),old['id']))
        ident=old['id']
    else:
        ident=secrets.token_hex(12)
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,1,?,?,?)',(ident,kind,dept,user['username'],dumps(payload),now(),now()))
    event(db,ident,user['username'],'修改或分配任务')
    bump(db)
    return {'id':ident}


def event(db,ident,actor,action,note=''):
    db.execute('INSERT INTO events(task,actor,at,action,note) VALUES(?,?,?,?,?)',(ident,actor,now(),action,text(note,1000)))
    audit(db,actor,'task_event',ident+':'+action)


def task_action(db,user,data):
    p=Policy(db,user); task=p.tasks.get(data.get('id'))
    require(task is not None,'任务不存在。',404)
    require(p.task_visible(task))
    require(task['version']==data.get('version'),'任务已更新，请重新打开。',409)
    d=task['data']; action=data.get('action'); executor=user['username'] in d['members']; manager=p.task_manage(task)
    if action=='cancel':
        require(manager); require(d['status'] not in ('已完成','已取消'),'任务已关闭。',409)
        text(data.get('note',''),1000,1); d['status']='已取消'
    else:
        require(active_task(db,task,p.tasks),'任务授权已失效或安排已变化，请联系分配人。',403)
        if action=='start':
            require(executor and d['status']=='待开始'); d['status']='进行中'
        elif action=='progress':
            require(executor or manager)
            require(d['status'] in ('待开始','进行中'),'当前任务不能登记进度。',409)
            if task['kind']=='notice':
                cid=data.get('candidate'); require(cid in d['candidates'])
                require(data.get('status') in ('待联系','已联系','需跟进'),'联系状态无效。',400)
                d['progress'][cid]={'status':data['status'],'note':text(data.get('note',''),1000),'by':user['username'],'at':now()}
            d['status']='进行中'
        elif action=='submit':
            require(executor and d['status'] in ('待开始','进行中'))
            if task['kind']=='interview':
                for name in d['reviewers']:
                    for cid in d['candidates']:
                        require(db.execute("SELECT 1 FROM reviews WHERE candidate=? AND department=? AND author=? AND status='submitted'",(cid,task['department'],name)).fetchone(),'仍有个人面试意见未提交。',409)
            if task['kind']=='notice': require(all(v['status']=='已联系' for v in d['progress'].values()),'仍有待联系或需跟进学生。',409)
            d['status']='已完成' if d.get('private') else '待审核'
        elif action in ('approve','return'):
            require(manager and d['status']=='待审核')
            if action=='return': text(data.get('note',''),1000,1)
            d['status']='已完成' if action=='approve' else '进行中'
        else: raise APIError(400,'任务操作无效。')
    db.execute('UPDATE tasks SET version=version+1,data=?,updated=? WHERE id=?',(dumps(d),now(),task['id']))
    event(db,task['id'],user['username'],action,data.get('note',''))
    bump(db)
    return {'ok':True}


def review_action(db,user,data):
    p=Policy(db,user); action=data.get('action'); cid=text(data.get('candidate'),40,1); dept=data.get('department')
    author=data.get('author') if action=='return' else user['username']
    old=db.execute('SELECT * FROM reviews WHERE candidate=? AND department=? AND author=?',(cid,dept,author)).fetchone()
    if old: require(data.get('version')==old['version'],'个人评价已更新。',409)
    if action=='return':
        require(old is not None and old['status']=='submitted' and p.manage(dept))
        note=text(data.get('reason'),1000,1)
        db.execute("UPDATE reviews SET status='draft',version=version+1 WHERE id=?",(old['id'],))
        event(db,old['task'],user['username'],'退回个人意见',note)
        bump(db)
        return {'ok':True}
    require(action in ('draft','submit'),'评价操作无效。',400)
    task=p.tasks.get(data.get('task'))
    require(task is not None and task['department']==dept and p.review_allowed(task,cid), '需要有效面试任务和本人面试资格。')
    require(not old or old['status']=='draft','已提交意见需由主要负责人退回后再改。',409)
    r=data.get('record',{})
    require(isinstance(r,dict) and set(r)<= {'dims','decision','score','note'},'个人评价字段不正确。',400)
    record={'candidate':cid,'dept':dept,'dims':r.get('dims'),'decision':r.get('decision'),'score':text(str(r.get('score','')),10),'note':text(r.get('note',''),2000)}
    check=empty_state(); c=next(c for c in p.state['candidates'] if c['id']==cid); check['candidates']=[c];check['records']=[record];validate_state(check)
    if record['score']: require(0<=float(record['score'])<=100,'评分应为0—100。',400)
    if p.state['rules'].get('preciseScore') and action=='submit': require(bool(record['score']),'精确评分已开启，请填写评分。',400)
    ident=old['id'] if old else secrets.token_hex(12)
    db.execute('INSERT INTO reviews VALUES(?,?,?,?,?, ?,?,?) ON CONFLICT(candidate,department,author) DO UPDATE SET task=excluded.task,version=reviews.version+1,status=excluded.status,data=excluded.data',
               (ident,cid,dept,user['username'],task['id'],(old['version']+1 if old else 1),'submitted' if action=='submit' else 'draft',dumps(record)))
    audit(db,user['username'],'review_'+action,ident)
    bump(db)
    return {'ok':True}


def list_reviews(db,user):
    p=Policy(db,user); ids={c['id'] for c in p.state['candidates'] if p.visible(c)}
    result=[]
    for r in db.execute('SELECT * FROM reviews ORDER BY id'):
        if r['candidate'] not in ids: continue
        if r['author']!=user['username'] and not (r['status']=='submitted' and p.manage(r['department'])): continue
        result.append({**{k:r[k] for k in ('id','candidate','department','author','task','version','status')},'record':json.loads(r['data'])})
    return result


def contact(db,user,data):
    p=Policy(db,user); cid=data.get('candidate'); c=next((c for c in p.state['candidates'] if c['id']==cid),None)
    require(c is not None and p.visible(c))
    if not p.manage(p.dept):
        t=p.tasks.get(data.get('task'))
        require(t and t['kind']=='notice' and t['department']==p.dept and user['username'] in t['data']['members'] and active_task(db,t,p.tasks) and cid in t['data']['candidates'] and 'contact' in t['data']['fields'])
    audit(db,user['username'],'contact',cid)
    return {'contact': c.get('contact','')}


def notification_text(db,user,data):
    p=Policy(db,user); t=p.tasks.get(data.get('task')); cid=data.get('candidate')
    require(t and t['kind']=='notice' and p.task_visible(t) and active_task(db,t,p.tasks) and cid in t['data']['candidates'])
    c=next((c for c in p.state['candidates'] if c['id']==cid and t['department'] in c['prefs']),None)
    require(c is not None)
    s=next((s for s in p.state['schedules'] if s['candidate']==cid and s['dept']==t['department']),{})
    values={'姓名':c['name'],'学号':cid,'学院':c['college'],'部门':t['department'],'联系电话':c.get('contact',''),
            '面试日期':s.get('date',''),'面试时间':s.get('time',''),'面试地点':s.get('room',''),'面试形式':'单面' if p.state['interviewConfig'][t['department']]['mode']=='single' else '群面',
            '志愿顺序':str(c['prefs'].index(t['department'])+1),'录取状态':next((m['dept'] for m in p.state['matches'] if m['candidate']==cid),'未录取')}
    rendered=re.sub(r'\{([^{}]+)\}',lambda m:values.get(m[1],m[0]),t['data']['template'])
    require('{联系电话}' not in t['data']['template'] or p.manage(t['department']) or 'contact' in t['data']['fields'])
    audit(db,user['username'],'notification_text',t['id']+':'+cid)
    return {'text':rendered}


def generate_matches(state):
    assigned=set(); matches=[]
    by_id={c['id']:c for c in state['candidates']}
    def eligible(cid,dept): return next((r.get('decision') for r in state['records'] if r['candidate']==cid and r['dept']==dept),None)!='不建议'
    for pref in range(3):
        for dept in MATCH_ORDER:
            remaining=state['quotas'][dept]-sum(m['dept']==dept for m in matches)
            for cid in state['rankings'][dept]:
                if remaining<=0: break
                c=by_id[cid]
                if cid in assigned or len(c['prefs'])<=pref or c['prefs'][pref]!=dept or not eligible(cid,dept): continue
                matches.append({'candidate':cid,'dept':dept,'pref':pref+1,'reason':f'{pref+1} 志愿，部门排序第 {state["rankings"][dept].index(cid)+1}'})
                assigned.add(cid);remaining-=1
    if state['rules'].get('autoTransfer'):
        for dept in MATCH_ORDER:
            remaining=state['quotas'][dept]-sum(m['dept']==dept for m in matches)
            ids=state['rankings'][dept]+[c['id'] for c in state['candidates'] if c['id'] not in state['rankings'][dept]]
            for cid in ids:
                if remaining<=0: break
                if cid in assigned or not eligible(cid,dept): continue
                index=state['rankings'][dept].index(cid)+1 if cid in state['rankings'][dept] else 0
                matches.append({'candidate':cid,'dept':dept,'pref':0,'reason':f'自动调剂至 {dept}'+(f'（部门排序第 {index}）' if index else '')})
                assigned.add(cid);remaining-=1
    return matches


def apply_business(db,user,data):
    version,state=read_state(db); p=Policy(db,user,state)
    operations=data.get('operations')
    require(isinstance(operations,list) and 0<len(operations)<=20000,'请提交具体业务操作。',400)
    request_id=text(data.get('request_id'),80,8)
    fingerprint=hashlib.sha256(dumps(operations).encode()).hexdigest()
    seen=db.execute('SELECT body_hash FROM requests WHERE username=? AND request_id=?',(user['username'],request_id)).fetchone()
    if seen:
        require(seen[0]==fingerprint,'重复请求ID对应内容不同。',409)
        return bootstrap(db,user)
    require(data.get('version')==version,'其他人已更新业务，本次未覆盖服务器，请保留编辑后重读。',409)
    original=copy.deepcopy(state)
    for op in operations:
        require(isinstance(op,dict) and set(op)<= {'kind','action','item','key'},'业务操作结构不正确。',400)
        kind,action,item=op.get('kind'),op.get('action'),op.get('item')
        require(action in ('upsert','delete'),'操作类型不正确。',400)
        if kind in ('candidate','schedule','record'):
            require(isinstance(item,dict),'业务对象格式不正确。',400)
            if kind=='candidate':
                require(p.admin)
                require(set(item)<= {'id','name','college','prefs','free','slots','availability','contact','note','status','deptSlots'},'报名对象字段不正确。',400)
                key=item.get('id'); collection='candidates'; find=lambda r:r['id']==key
                if action=='upsert' and state['rules'].get('lockAfterDeadline') and state['timeline'].get('deadline'):
                    deadline=datetime.fromisoformat(state['timeline']['deadline'])
                    # Naive original values are interpreted as Beijing local time.
                    from datetime import timezone,timedelta
                    deadline=deadline.replace(tzinfo=timezone(timedelta(hours=8))) if deadline.tzinfo is None else deadline
                    old=next((c for c in state['candidates'] if c['id']==key),None)
                    require(now()<deadline.timestamp() or old and old['prefs']==item.get('prefs'),'志愿已截止；如需补录请先明确关闭截止锁定。',409)
            else:
                dept,cid=item.get('dept'),item.get('candidate')
                c=next((c for c in original['candidates'] if c['id']==cid),None)
                require(dept in DEPARTMENTS and (p.manage(dept) or kind=='schedule' and dept==p.dept and c and p.visible(c) and p.schedule(cid)))
                if kind=='schedule':
                    require(set(item)<= {'id','candidate','dept','date','time','endTime','room','auto','matchState','source'},'排期对象字段不正确。',400)
                    collection='schedules'; key=item.get('id');find=lambda r:r['id']==key
                    old=next((s for s in state[collection] if s['id']==key),None)
                    require(not old or old['dept']==dept and old['candidate']==cid,'不能把排期改到其他学生或部门。')
                else:
                    require(set(item)<= {'candidate','dept','dims','decision','score','note'},'部门记录字段不正确。',400)
                    collection='records';find=lambda r:r['candidate']==cid and r['dept']==dept
            index=next((i for i,r in enumerate(state[collection]) if find(r)),None)
            if action=='delete': state[collection]=[r for r in state[collection] if not find(r)]
            elif index is None: state[collection].append(copy.deepcopy(item))
            else: state[collection][index]=copy.deepcopy(item)
        elif kind in ('ranking','departmentAvailability','interviewConfig'):
            dept=op.get('key'); require(dept in DEPARTMENTS)
            require(p.manage(dept) or kind!='ranking' and dept==p.dept and p.settings())
            require(action=='upsert')
            state['rankings' if kind=='ranking' else kind][dept]=copy.deepcopy(item)
        elif kind in ('quotas','rules','timeline','notificationTemplates'):
            require(p.admin and action=='upsert'); state[kind]=copy.deepcopy(item)
        elif kind=='matches':
            require(p.admin and action=='upsert'); state['matches']=generate_matches(state) if item else []
        else: raise APIError(400,'未知业务操作。')
    # Preserve the user's ranking/volunteer order; clean references only when edited by admin.
    ids={c['id']:c for c in state['candidates']}
    if p.admin:
        state['schedules']=[s for s in state['schedules'] if s['candidate'] in ids and s['dept'] in ids[s['candidate']]['prefs']]
        state['records']=[r for r in state['records'] if r['candidate'] in ids and r['dept'] in ids[r['candidate']]['prefs']]
        state['matches']=[m for m in state['matches'] if m['candidate'] in ids]
        state['rankings']={d:[cid for cid in rank if cid in ids and d in ids[cid]['prefs']] for d,rank in state['rankings'].items()}
    validate_state(state)
    for s in state['schedules']:
        for other in state['schedules']:
            if s['id']==other['id'] or s['candidate']!=other['candidate']: continue
            if s['date']==other['date'] and s['time']<other['endTime'] and s['endTime']>other['time']:
                # Historic conflicts may remain, but new/changed conflicting schedules are rejected.
                old_s=next((v for v in original['schedules'] if v==s),None)
                old_o=next((v for v in original['schedules'] if v==other),None)
                require(old_s and old_o,'学生已有重叠面试，不能应用本次排期。',409)
    for c in state['candidates']:
        prefs=set(c['prefs']); scheduled={s['dept'] for s in state['schedules'] if s['candidate']==c['id']}; recorded={r['dept'] for r in state['records'] if r['candidate']==c['id']}
        c['status']='已完成' if prefs<=recorded else '已安排' if prefs<=scheduled else '待处理' if scheduled else '待安排'
    for task in all_tasks(db).values():
        d=task['data']
        if d['status'] in ('已完成','已取消'):continue
        invalid_candidate=any(cid not in ids or task['department'] not in ids[cid]['prefs'] for cid in d['candidates'])
        if invalid_candidate or task['kind']=='interview' and d['fingerprint']!=schedule_fingerprint(state['schedules'],d['schedule_ids']):
            d['stale']=True
            db.execute('UPDATE tasks SET data=?,version=version+1 WHERE id=?',(dumps(d),task['id']))
            event(db,task['id'],user['username'],'安排变化待确认')
    db.execute('UPDATE state SET version=version+1,payload=? WHERE id=1',(dumps(state),))
    db.execute('INSERT INTO requests VALUES(?,?,?,?)',(user['username'],request_id,fingerprint,now()))
    db.execute('DELETE FROM requests WHERE created<?',(now()-86400,))
    bump(db)  # Re-evaluate changed preference and task boundaries before showing cached data.
    audit(db,user['username'],'business',str(len(operations)))
    return bootstrap(db,user)


def restore_business(db,user,session,data,preview=False):
    require(user['role']=='admin')
    state=data.get('state');validate_state(state)
    digest=hashlib.sha256(dumps(state).encode()).hexdigest()
    counts={k:len(state[k]) for k in ('candidates','schedules','records','matches')}
    if preview:return {'hash':digest,'counts':counts}
    recent(session)
    version,previous=read_state(db)
    require(data.get('version')==version,'业务已更新，请重新预览恢复文件。',409)
    require(hmac.compare_digest(text(data.get('hash'),64,64),digest),'预览后的备份内容已变化。',409)
    # Preserve the previous business snapshot inside the private DB transaction.
    # Accounts, sessions, tasks and personal opinions are never restored from JSON.
    db.execute('CREATE TABLE IF NOT EXISTS restore_history(id INTEGER PRIMARY KEY,at INTEGER NOT NULL,username TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL)')
    db.execute('INSERT INTO restore_history(at,username,version,payload) VALUES(?,?,?,?)',(now(),user['username'],version,dumps(previous)))
    db.execute('UPDATE state SET payload=?,version=version+1 WHERE id=1',(dumps(state),))
    for task in all_tasks(db).values():
        if task['kind']!='general' and task['data']['status'] not in ('已完成','已取消'):
            d=task['data'];d['stale']=True
            db.execute('UPDATE tasks SET data=?,version=version+1 WHERE id=?',(dumps(d),task['id']))
            event(db,task['id'],user['username'],'业务恢复后待重新确认')
    bump(db);audit(db,user['username'],'restore_business',str(version))
    return bootstrap(db,user)
