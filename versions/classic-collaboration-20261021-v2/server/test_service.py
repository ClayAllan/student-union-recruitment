"""Integration fixtures are synthetic and require RECRUIT_TEST_DIR in 99_工作区."""
import concurrent.futures
import copy
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app
import service as s
from errors import APIError


class Client:
    def __init__(self,url):
        self.url,self.csrf=url,''
        self.cookies=http.cookiejar.CookieJar()
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self,path,data=None,csrf=True,headers=None):
        h={'Content-Type':'application/json'} if data is not None else {}
        if csrf:h['X-CSRF-Token']=self.csrf
        h.update(headers or {})
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
        try:
            with self.opener.open(req,timeout=15) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as r:return r.code,json.load(r)

    def login(self,name,password):
        code,result=self.request('/api/login',{'username':name,'password':password})
        if code==200:self.csrf=result['csrf']
        return code,result


class CollaborationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['RECRUIT_TEST_DIR'])
        self.path=str(Path(self.tmp.name)/'test.sqlite3');s.init_db(self.path)
        self.password='Test secret 2026'
        for name,role,dept in [('manager','admin',''),('lead','lead','权益部'),('head','head','权益部'),('staff','staff','权益部'),('worker','staff','权益部'),('office','lead','办公室')]:
            s.set_user(self.path,name,role,self.password,dept,name,True)
        state=s.empty_state()
        for cid,prefs in [('QA1',['权益部','办公室']),('QA2',['办公室']),('QA3',['权益部'])]:
            state['candidates'].append({'id':cid,'name':'虚构'+cid,'college':'测试学院','prefs':prefs,'availability':[],'slots':[],'contact':'虚构联系方式-'+cid,'note':'内部备注-'+cid,'status':'待安排'})
            for d in prefs:state['rankings'][d].append(cid)
        state['schedules']=[{'id':1,'candidate':'QA1','dept':'权益部','date':'2030-10-09','time':'19:00','endTime':'19:05','room':'测试203'}, {'id':2,'candidate':'QA1','dept':'办公室','date':'2030-10-09','time':'20:00','endTime':'20:05','room':'测试209'}, {'id':3,'candidate':'QA3','dept':'权益部','date':'2030-10-09','time':'19:05','endTime':'19:10','room':'测试203'}]
        with s.connect(self.path) as db:db.execute('UPDATE state SET payload=?',(s.dumps(state),))
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.make_handler(self.path))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.manager=self.client('manager')

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def client(self,name):
        c=Client(self.url);self.assertEqual(c.login(name,self.password)[0],200);return c

    def reauth(self):self.assertEqual(self.manager.request('/api/me/reauth',{'password':self.password})[0],200)

    def task(self,client=None,**change):
        body={'kind':'interview','department':'权益部','title':'虚构面试任务','description':'测试分工','members':['head'],'assignee':'head','reviewers':['head'], 'candidates':['QA1'],'schedule_ids':['1'],'fields':[],'due':s.now()-20,'access_until':s.now()+3600,'parent':'','allow_schedule':True,'allow_settings':False,'private':False,'template':'','checklist':'','priority':'普通'}
        body.update(change);c=client or self.manager
        if body.get('parent') and 'access_until' not in change:
            body['access_until']=min(body['access_until'],self.task_get(c,body['parent'])['data']['access_until'])
        code,result=c.request('/api/tasks/save',body);self.assertEqual(code,200,result);return result['id']

    def task_get(self,client,ident):return next(t for t in client.request('/api/tasks')[1]['tasks'] if t['id']==ident)

    def business(self,client,operations,**change):
        body={'version':client.request('/api/bootstrap')[1]['version'],'operations':operations,'request_id':secrets.token_hex(12)};body.update(change)
        return client.request('/api/business',body)

    def review(self,client,ident,action='submit',**extra):
        body={'action':action,'task':ident,'candidate':'QA1','department':'权益部','record':{'dims':['稳定']*3,'decision':'推荐','score':'88','note':'虚构独立意见'}};body.update(extra);return client.request('/api/reviews/action',body)

    def test_anonymous_and_private_files(self):
        c=Client(self.url)
        for route in ('/api/bootstrap','/api/tasks','/api/admin/users','/api/reviews'):self.assertEqual(c.request(route)[0],401)
        for route in ('/server/app.py','/preview.sqlite3','/../server/app.py'):self.assertEqual(c.request(route)[0],404)
        self.assertEqual(c.request('/api/health')[1]['max_sessions'],1)
        with c.opener.open(self.url) as response:self.assertIn('auth-pending',response.read().decode())

    def test_password_boundaries_unicode_and_storage(self):
        for value in ('Abc123!','x'*129,'12345678'):
            with self.assertRaises(APIError):s.set_user(self.path,'helper','staff',value,'权益部')
        for value in ('Abc12!xy','Ab 12!中文','Abc '+'z'*124):
            s.set_user(self.path,'helper','staff',value,'权益部');self.assertEqual(Client(self.url).login('helper',value)[0],200)
        with s.connect(self.path) as db:self.assertNotEqual(db.execute("SELECT password FROM users WHERE username='helper'").fetchone()[0],value)

    def test_single_session_invalidates_old_not_failed_password(self):
        first=self.client('head');failed=Client(self.url)
        self.assertEqual(failed.login('head','incorrect')[0],401)
        self.assertEqual(first.request('/api/bootstrap')[0],200)
        second=self.client('head');self.assertEqual(first.request('/api/bootstrap')[0],401);self.assertEqual(second.request('/api/bootstrap')[0],200)
        self.assertEqual(second.request('/api/session',headers={'X-Real-IP':'192.0.2.17'})[0],200)
        self.assertEqual(second.request('/api/session')[0],200)

    def test_parallel_login_only_one_valid(self):
        clients=[Client(self.url),Client(self.url)]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda c:c.login('staff',self.password)[0],clients))
        self.assertEqual(results,[200,200]);self.assertEqual(sorted(c.request('/api/session')[0] for c in clients),[200,401])

    def test_failed_login_transaction_keeps_previous(self):
        first=self.client('staff')
        with patch('app.secrets.token_urlsafe',side_effect=RuntimeError('synthetic failure')):self.assertEqual(Client(self.url).login('staff',self.password)[0],500)
        self.assertEqual(first.request('/api/session')[0],200)

    def test_scope_and_sensitive_field_projection(self):
        lead=self.client('lead');head=self.client('head')
        value=lead.request('/api/bootstrap')[1]['state'];self.assertEqual([c['id'] for c in value['candidates']],['QA1','QA3'])
        self.assertTrue(all(c['contact']=='' and c['note']=='' for c in value['candidates']))
        self.assertEqual(head.request('/api/bootstrap')[1]['state']['candidates'],[])
        self.assertEqual(head.request('/api/admin/users')[0],403)
        self.task();value=head.request('/api/bootstrap')[1]['state'];self.assertEqual([c['id'] for c in value['candidates']],['QA1']);self.assertEqual(value['candidates'][0]['prefs'],['权益部'])
        self.assertNotIn('测试209',s.dumps(value));self.assertEqual(head.request('/api/contact',{'candidate':'QA1'})[0],403)

    def test_account_management_first_change_and_no_extra_activation(self):
        payload={'username':'newperson','display_name':'虚构成员','role':'staff','department':'权益部','can_interview':True}
        self.assertEqual(self.manager.request('/api/admin/users/create',payload)[0],428);self.reauth()
        code,result=self.manager.request('/api/admin/users/create',payload);self.assertEqual(code,200,result)
        self.assertEqual(len(result['temporary_password']),16)
        c=Client(self.url);self.assertEqual(c.login('newperson',result['temporary_password'])[0],200)
        self.assertEqual(c.request('/api/bootstrap')[0],428)
        self.assertEqual(c.request('/api/me/password',{'old_password':result['temporary_password'],'password':'New12!xy'})[0],200)
        self.assertEqual(c.request('/api/session')[0],401);self.assertEqual(c.login('newperson','New12!xy')[0],200);self.assertEqual(c.request('/api/bootstrap')[0],200)

    def test_last_admin_guard_and_role_change_revokes(self):
        self.reauth();rows=self.manager.request('/api/admin/users')[1]['users'];admin=next(u for u in rows if u['username']=='manager')
        body={**admin,'active':False}
        self.assertEqual(self.manager.request('/api/admin/users/edit',body)[0],409)
        head=self.client('head');u=next(u for u in rows if u['username']=='head');body={**u,'role':'staff'}
        self.assertEqual(self.manager.request('/api/admin/users/edit',body)[0],200);self.assertEqual(head.request('/api/session')[0],401)

    def test_roles_departments_sorted(self):
        self.assertEqual(list(s.ROLES),['admin','lead','head','staff'])
        self.assertEqual(s.DEPARTMENTS,['办公室','公关部','活动部','权益部','实践部','宣媒部'])
        data=self.manager.request('/api/bootstrap')[1];self.assertEqual(data['state']['quotas'],s.QUOTAS)
        users=self.manager.request('/api/admin/users')[1]['users'];self.assertEqual([u['role'] for u in users],sorted([u['role'] for u in users],key=list(s.ROLES).index))

    def test_head_delegation_subset_and_no_self_escalation(self):
        root=self.task();head=self.client('head');staff=self.client('staff')
        child=self.task(head,members=['staff'],assignee='staff',reviewers=['staff'],parent=root,allow_schedule=False)
        self.assertEqual([c['id'] for c in staff.request('/api/bootstrap')[1]['state']['candidates']],['QA1'])
        base={'kind':'notice','title':'越权','department':'权益部','members':['staff'],'assignee':'staff','reviewers':[],'candidates':['QA3'],'schedule_ids':[],'fields':['contact'],'access_until':s.now()+300,'due':0,'parent':root}
        self.assertEqual(head.request('/api/tasks/save',base)[0],403)
        self.assertEqual(staff.request('/api/tasks/save',base)[0],403)
        t=self.task_get(self.manager,root)
        self.assertEqual(self.manager.request('/api/tasks/action',{'id':root,'version':t['version'],'action':'cancel','note':'虚构撤销'})[0],200)
        self.assertEqual(staff.request('/api/bootstrap')[1]['state']['candidates'],[])
        self.assertFalse(self.task_get(staff,child)['effective'])

    def test_cross_department_and_hierarchy_assignment_rejected(self):
        lead=self.client('lead')
        payload={'kind':'general','title':'虚构','department':'办公室','members':['office'],'assignee':'office','reviewers':[],'candidates':[],'schedule_ids':[],'fields':[],'access_until':s.now()+600,'due':0}
        self.assertEqual(lead.request('/api/tasks/save',payload)[0],403)
        payload.update(department='权益部',members=['lead'],assignee='lead')
        self.assertEqual(lead.request('/api/tasks/save',payload)[0],403)

    def test_general_and_private_tasks_do_not_grant_student_access(self):
        staff=self.client('staff');head=self.client('head')
        ident=self.task(head,kind='general',members=['staff'],assignee='staff',reviewers=[],candidates=[],schedule_ids=[],allow_schedule=False)
        self.assertEqual(staff.request('/api/bootstrap')[1]['state']['candidates'],[])
        for action in ('start','progress','submit'):
            task=self.task_get(staff,ident);self.assertEqual(staff.request('/api/tasks/action',{'id':ident,'version':task['version'],'action':action,'note':'虚构进度'})[0],200)
        task=self.task_get(head,ident);self.assertEqual(task['data']['status'],'待审核')
        self.assertEqual(staff.request('/api/tasks/action',{'id':ident,'version':task['version'],'action':'approve'})[0],403)
        self.assertEqual(head.request('/api/tasks/action',{'id':ident,'version':task['version'],'action':'approve'})[0],200)
        private=self.task(staff,kind='general',members=['staff'],assignee='staff',reviewers=[],candidates=[],schedule_ids=[],allow_schedule=False,private=True)
        self.assertNotIn(private,[t['id'] for t in self.manager.request('/api/tasks')[1]['tasks']])

    def test_expiry_changes_scope_without_database_mutation(self):
        self.task(access_until=s.now()+60);head=self.client('head');before=head.request('/api/session')[1]['scope_revision']
        future=s.now()+61
        with patch('service.now',return_value=future):
            self.assertNotEqual(head.request('/api/session')[1]['scope_revision'],before)
            self.assertEqual(head.request('/api/bootstrap')[1]['state']['candidates'],[])
        self.assertTrue(self.task_get(head,next(iter(self.manager.request('/api/tasks')[1]['tasks']))['id'])['overdue'])

    def test_notice_scope_text_progress_and_duplicate_protection(self):
        staff=self.client('staff');ident=self.task(kind='notice',members=['staff'],assignee='staff',reviewers=[],schedule_ids=[],fields=['contact'],allow_schedule=False,template='{姓名} {部门} {面试日期} {联系电话}')
        self.assertEqual(staff.request('/api/contact',{'task':ident,'candidate':'QA1'})[1]['contact'],'虚构联系方式-QA1')
        self.assertEqual(staff.request('/api/contact',{'task':ident,'candidate':'QA2'})[0],403)
        self.assertIn('2030-10-09',staff.request('/api/notification-text',{'task':ident,'candidate':'QA1'})[1]['text'])
        task=self.task_get(staff,ident);self.assertEqual(staff.request('/api/tasks/action',{'id':ident,'version':task['version'],'action':'submit'})[0],409)
        self.assertEqual(staff.request('/api/tasks/action',{'id':ident,'version':task['version'],'action':'progress','candidate':'QA1','status':'已联系','note':'人工确认'})[0],200)
        task=self.task_get(staff,ident);self.assertEqual(staff.request('/api/tasks/action',{'id':ident,'version':task['version'],'action':'submit'})[0],200)

    def test_reviews_independent_and_draft_privacy(self):
        ident=self.task(members=['head','staff'],assignee='head',reviewers=['head','staff']);head=self.client('head');staff=self.client('staff');lead=self.client('lead')
        self.assertEqual(self.review(head,ident,'draft')[0],200)
        self.assertEqual(lead.request('/api/reviews')[1]['reviews'],[]);self.assertEqual(staff.request('/api/reviews')[1]['reviews'],[])
        own=head.request('/api/reviews')[1]['reviews'][0]
        self.assertEqual(self.review(head,ident,version=own['version'])[0],200)
        self.assertEqual(self.review(staff,ident)[0],200)
        self.assertEqual(len(lead.request('/api/reviews')[1]['reviews']),2)
        self.assertEqual(self.manager.request('/api/bootstrap')[1]['state']['records'],[])
        self.assertEqual(self.review(head,ident,version=2)[0],409)
        record=next(r for r in lead.request('/api/reviews')[1]['reviews'] if r['author']=='head')
        self.assertEqual(lead.request('/api/reviews/action',{'action':'return','candidate':'QA1','department':'权益部','author':'head','version':record['version'],'reason':'请补充表现'})[0],200)

    def test_forged_author_and_missing_qualification(self):
        ident=self.task(members=['head','staff'],assignee='head',reviewers=['head','staff']);staff=self.client('staff')
        self.assertEqual(self.review(staff,ident,author='manager')[0],200)
        self.assertEqual(staff.request('/api/reviews')[1]['reviews'][0]['author'],'staff')
        with s.connect(self.path) as db:db.execute("UPDATE users SET can_interview=0 WHERE username='head'")
        self.assertEqual(self.review(self.client('head'),ident)[0],403)

    def test_business_commands_permissions_and_conflict(self):
        head=self.client('head');lead=self.client('lead');self.task()
        denied=[{'kind':'quotas','action':'upsert','item':s.QUOTAS},{'kind':'candidate','action':'upsert','item':{'id':'QA1','name':'篡改'}},{'kind':'record','action':'upsert','item':{'candidate':'QA1','dept':'权益部'}}]
        for op in denied:self.assertEqual(self.business(head,[op])[0],403)
        payload={'kind':'interviewConfig','action':'upsert','key':'权益部','item':{**s.CONFIG,'groupDuration':30}}
        version=lead.request('/api/bootstrap')[1]['version']
        self.assertEqual(self.business(lead,[payload])[0],200)
        self.assertEqual(self.business(lead,[payload],version=version)[0],409)
        self.assertEqual(head.request('/api/state',{'version':0,'state':{}})[0],404)

    def test_business_idempotent_and_source_validation(self):
        operations=[{'kind':'rules','action':'upsert','item':{**s.empty_state()['rules'],'autoTransfer':True}}]
        body={'version':0,'operations':operations,'request_id':'replay-test-0001'}
        self.assertEqual(self.manager.request('/api/business',body)[0],200)
        self.assertEqual(self.manager.request('/api/business',body)[0],200)
        self.assertEqual(self.manager.request('/api/bootstrap')[1]['version'],1)
        body['operations'][0]['item']['autoTransfer']=False;self.assertEqual(self.manager.request('/api/business',body)[0],409)

    def test_changed_schedule_invalidates_task_and_parent(self):
        root=self.task();head=self.client('head');self.task(head,members=['staff'],assignee='staff',reviewers=['staff'],parent=root,allow_schedule=False)
        staff=self.client('staff');before=staff.request('/api/session')[1]['scope_revision']
        state=self.manager.request('/api/bootstrap')[1]['state'];item=copy.deepcopy(state['schedules'][0]);item['time']='18:00';item['endTime']='18:05'
        self.assertEqual(self.business(self.manager,[{'kind':'schedule','action':'upsert','item':item}])[0],200)
        self.assertNotEqual(staff.request('/api/session')[1]['scope_revision'],before);self.assertEqual(staff.request('/api/bootstrap')[1]['state']['candidates'],[])

    def test_authorized_draft_does_not_write(self):
        lead=self.client('lead');version=lead.request('/api/bootstrap')[1]['version'];op={'kind':'ranking','action':'upsert','key':'权益部','item':['QA3','QA1']}
        self.assertEqual(lead.request('/api/draft',{'operations':[op]})[0],200)
        self.assertEqual(lead.request('/api/bootstrap')[1]['version'],version)
        self.assertEqual(lead.request('/api/bootstrap')[1]['state']['rankings']['权益部'],['QA1','QA3'])
        self.assertEqual(self.client('staff').request('/api/draft',{'operations':[op]})[0],403)

    def test_csrf_origin_https_logout_and_throttling(self):
        self.assertEqual(self.manager.request('/api/tasks/save',{},csrf=False)[0],403)
        self.assertEqual(self.manager.request('/api/logout',{},headers={'Origin':'https://example.invalid'})[0],403)
        self.assertEqual(self.manager.request('/api/bootstrap',headers={'Host':'public.example'})[0],426)
        failed=Client(self.url)
        for _ in range(10):self.assertEqual(failed.login('unknown','incorrect')[0],401)
        self.assertEqual(failed.login('unknown','incorrect')[0],429)
        self.assertEqual(self.manager.request('/api/logout',{})[0],200);self.assertEqual(self.manager.request('/api/bootstrap')[0],401)

    def test_secure_cookie_and_untrusted_proxy_value(self):
        c=Client(self.url);self.assertEqual(c.request('/api/login',{'username':'staff','password':self.password},headers={'Host':'example.test','X-Forwarded-Proto':'https','X-Real-IP':'bad IP'})[0],200)
        cookie=next(iter(c.cookies));self.assertTrue(cookie.secure);self.assertEqual(cookie.name,app.SESSION_COOKIE)
        failed=Client(self.url);failed.request('/api/login',{'username':'missing','password':'wrong'},headers={'X-Real-IP':'not-an-ip'})
        with s.connect(self.path) as db:self.assertTrue(db.execute("SELECT 1 FROM login_attempts WHERE key='peer:127.0.0.1'").fetchone())

    def test_database_rejection_and_consistent_backup(self):
        other=Path(self.tmp.name)/'other.sqlite3'
        with context_closing(sqlite3.connect(other)) as db:db.execute('CREATE TABLE users(username TEXT,role TEXT,salt TEXT,password TEXT)');db.commit()
        before=other.read_bytes()
        with self.assertRaises(APIError):s.init_db(str(other))
        self.assertEqual(other.read_bytes(),before)
        dest=Path(self.tmp.name)/'backup.sqlite3'
        with s.connect(self.path) as db,context_closing(sqlite3.connect(dest)) as target:db.backup(target)
        s.init_db(str(dest))
        with s.connect(str(dest)) as db:self.assertEqual(len(s.read_state(db)[1]['candidates']),3)

    def test_original_matching_priority_preserved(self):
        with s.connect(self.path) as db:_,state=s.read_state(db)
        state['rules']['autoTransfer']=True
        matches=s.generate_matches(state)
        self.assertEqual({m['candidate']:m['dept'] for m in matches},{'QA1':'权益部','QA3':'权益部','QA2':'办公室'})
        self.assertEqual(state['candidates'][0]['prefs'],['权益部','办公室'])
        state['rankings']={d:[] for d in s.DEPARTMENTS}
        self.assertEqual(s.generate_matches(state)[0]['dept'],'权益部')


from contextlib import closing as context_closing
if __name__=='__main__':unittest.main(verbosity=2)
