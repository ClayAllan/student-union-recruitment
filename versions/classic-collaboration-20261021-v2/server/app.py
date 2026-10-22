"""Run this independent release with Python 3.10+ and an explicit private database."""
import argparse
import contextlib
import getpass
import hashlib
import hmac
import http.cookies
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import service as s
from service import init_db, connect, read_state, set_user, VERSION
from errors import APIError, require

PUBLIC=Path(__file__).resolve().parent.parent/'dist'
SESSION_COOKIE='classic_collaboration_session'
LOCK=threading.RLock()
MAX_BODY=4*1024*1024


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        server_version='Recruitment'

        def log_message(self,*_): pass

        def json_response(self,status,data,headers=None):
            payload=s.dumps(data).encode('utf-8')
            self.send_response(status)
            for key,value in {'Content-Type':'application/json; charset=utf-8','Content-Length':str(len(payload)), 'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',**(headers or {})}.items(): self.send_header(key,value)
            self.end_headers();self.wfile.write(payload)

        def body(self):
            require(self.headers.get_content_type()=='application/json','需要JSON请求。',415)
            try:
                length=int(self.headers.get('Content-Length','0'))
                require(0<length<=MAX_BODY,'请求大小无效。',413)
                data=json.loads(self.rfile.read(length),parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
                require(isinstance(data,dict),'请求格式不正确。',400)
                return data
            except (ValueError,UnicodeError):raise APIError(400,'请求格式不正确。')

        def secure(self):
            return self.client_address[0] in ('127.0.0.1','::1') and self.headers.get('X-Forwarded-Proto')=='https'

        def client_ip(self):
            peer=self.client_address[0]
            if peer in ('127.0.0.1','::1'):
                try:return str(ipaddress.ip_address(self.headers.get('X-Real-IP',peer)))
                except ValueError:pass
            return peer

        def transport(self):
            host=urlparse('http://'+self.headers.get('Host','')).hostname
            require(self.secure() or host in ('localhost','127.0.0.1','::1'),'公网登录与业务操作必须通过HTTPS。',426)

        def session(self,db,mutation=False):
            cookies=http.cookies.SimpleCookie()
            try:cookies.load(self.headers.get('Cookie',''))
            except http.cookies.CookieError:pass
            token=cookies.get(SESSION_COOKIE)
            digest=hashlib.sha256(token.value.encode()).hexdigest() if token else ''
            session=db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?',(digest,s.now())).fetchone()
            require(session is not None,'登录已失效，账号可能已在其他浏览器登录，请重新登录。',401)
            user=db.execute('SELECT * FROM users WHERE username=?',(session['username'],)).fetchone()
            require(user is not None and user['active'],'账号已停用，请联系总负责人。',401)
            if user['must_change']:require(user['temp_until']>s.now(),'临时密码已过期，请联系总负责人重置。',401)
            if mutation:require(hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']),'请求验证失败，请刷新。',403)
            return user,session

        def throttle(self,db,key,limit):
            require(db.execute('SELECT COUNT(*) FROM login_attempts WHERE key=? AND at>?',(key,s.now()-900)).fetchone()[0]<limit,'尝试过多，请15分钟后再试。',429)

        def login(self,db,data):
            name,password=data.get('username',''),data.get('password','')
            require(isinstance(name,str) and len(name)<=40 and isinstance(password,str) and len(password)<=128,'账号或密码格式无效。',400)
            keys=['user:'+name,'peer:'+self.client_ip()]
            self.throttle(db,keys[0],10);self.throttle(db,keys[1],60)
            user=db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
            valid=s.verify(user,password)
            if not valid or not user['active'] or user['must_change'] and user['temp_until']<=s.now():
                for key in keys:db.execute('INSERT INTO login_attempts VALUES(?,?)',(key,s.now()))
                # Failed attempt persistence is deliberate; it must not replace a valid login.
                db.commit()
                raise APIError(401,'用户名、密码不正确或账号不可用。')
            db.execute('DELETE FROM login_attempts WHERE at<? OR key=?',(s.now()-900,keys[0]))
            token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE username=?',(name,))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),name,csrf,s.now()+8*3600,0,s.now(),self.headers.get('User-Agent','')[:200]))
            db.execute('UPDATE users SET last_login=? WHERE username=?',(s.now(),name))
            s.audit(db,name,'login_replaces_session')
            user=db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
            cookie=SESSION_COOKIE+'='+token+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800'+('; Secure' if self.secure() else '')
            return {'user':s.public_user(user),'csrf':csrf},{'Set-Cookie':cookie}

        def do_GET(self):
            try:
                route=urlparse(self.path).path
                if route in ('/api/health','/api/config'):
                    return self.json_response(200,{'ok':True,'version':VERSION,'application':s.APPLICATION,'schema':s.SCHEMA,'authentication':'password','max_sessions':1})
                if route.startswith('/api/'):
                    self.transport()
                    with LOCK,connect(db_path) as db:
                        user,session=self.session(db)
                        if route=='/api/session':
                            return self.json_response(200,{'user':s.public_user(user),'csrf':session['csrf'],'revision':db.execute("SELECT value FROM metadata WHERE key='auth_revision'").fetchone()[0], 'scope_revision':s.scope_revision(db,user), 'version':s.read_state(db)[0]})
                        require(not user['must_change'],'请先设置正式密码。',428)
                        if route in ('/api/bootstrap','/api/state'):return self.json_response(200,s.bootstrap(db,user))
                        if route=='/api/members':return self.json_response(200,{'members':s.member_list(db,user)})
                        if route=='/api/tasks':
                            p=s.Policy(db,user)
                            return self.json_response(200,{'tasks':[s.task_public(db,user,t,p) for t in p.tasks.values() if p.task_visible(t)]})
                        if route=='/api/reviews':return self.json_response(200,{'reviews':s.list_reviews(db,user)})
                        if route=='/api/admin/users':
                            require(user['role']=='admin')
                            users=[]
                            for row in db.execute('SELECT * FROM users'):
                                item=s.public_user(row);login=db.execute('SELECT created,agent,expires FROM sessions WHERE username=?',(row['username'],)).fetchone()
                                item['session']=dict(login) if login and login['expires']>s.now() else None
                                users.append(item)
                            users.sort(key=lambda u:(list(s.ROLES).index(u['role']),s.DEPARTMENTS.index(u['department']) if u['department'] else -1,u['display_name'],u['username']))
                            return self.json_response(200,{'users':users})
                        if route=='/api/audit':
                            require(user['role']=='admin')
                            return self.json_response(200,{'entries':[dict(r) for r in db.execute('SELECT at,username,action,detail FROM audit ORDER BY id DESC LIMIT 100')]})
                    raise APIError(404,'接口不存在。')
                name='index.html' if route=='/' else route.lstrip('/')
                require(name in ('index.html','login.js','team.js','team.css','xlsx.full.min.js','student-union-recruitment-template.xlsx'),'文件不存在。',404)
                content=(PUBLIC/name).read_bytes()
                mime='text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'application/javascript' if name.endswith('.js') else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                self.send_response(200)
                for key,value in {'Content-Type':mime+'; charset=utf-8','Content-Length':str(len(content)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','X-Frame-Options':'DENY'}.items():self.send_header(key,value)
                self.end_headers();self.wfile.write(content)
            except APIError as e:self.json_response(e.status,{'error':e.message})
            except (KeyError,TypeError,ValueError):self.json_response(400,{'error':'数据格式不正确。'})
            except Exception:self.json_response(500,{'error':'服务器处理失败，请联系总负责人。'})

        def do_POST(self):
            try:
                # Consume a bounded body before rejecting origin/transport, avoiding
                # a socket reset with unread request bytes on Windows.
                data=self.body()
                origin=self.headers.get('Origin')
                require(not origin or urlparse(origin).netloc==self.headers.get('Host'),'不允许跨站提交。')
                self.transport();route=urlparse(self.path).path;headers={}
                with LOCK,connect(db_path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    if route=='/api/login':
                        result,headers=self.login(db,data)
                    else:
                        user,session=self.session(db,True)
                        if route=='/api/logout':
                            db.execute('DELETE FROM sessions WHERE token_hash=?',(session['token_hash'],));result={'ok':True}
                            headers={'Set-Cookie':SESSION_COOKIE+'=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'+('; Secure' if self.secure() else '')}
                        elif route=='/api/me/password':
                            key='reauth:'+user['username'];self.throttle(db,key,10)
                            if not s.verify(user,data.get('old_password')):
                                db.execute('INSERT INTO login_attempts VALUES(?,?)',(key,s.now()));db.commit();raise APIError(403,'旧密码不正确。')
                            value=s.password_check(data.get('password'),user['username']);salt=secrets.token_hex(16)
                            db.execute('UPDATE users SET salt=?,password=?,must_change=0,temp_until=0,revision=revision+1 WHERE username=?',(salt,s.password_hash(value,salt),user['username']))
                            db.execute('DELETE FROM sessions WHERE username=?',(user['username'],));s.bump(db);s.audit(db,user['username'],'password_change');result={'ok':True,'relogin':True}
                        else:
                            require(not user['must_change'],'请先设置正式密码。',428)
                            if route=='/api/me/reauth':
                                key='reauth:'+user['username'];self.throttle(db,key,10)
                                if not s.verify(user,data.get('password')):
                                    db.execute('INSERT INTO login_attempts VALUES(?,?)',(key,s.now()));db.commit();raise APIError(403,'密码不正确。')
                                db.execute('DELETE FROM login_attempts WHERE key=?',(key,));db.execute('UPDATE sessions SET verified=? WHERE token_hash=?',(s.now(),session['token_hash']));result={'ok':True}
                            elif route.startswith('/api/admin/users/'):
                                result=s.account_action(db,user,session,route.rsplit('/',1)[-1],data)
                            elif route=='/api/tasks/save': result=s.save_task(db,user,data)
                            elif route=='/api/tasks/action': result=s.task_action(db,user,data)
                            elif route=='/api/reviews/action': result=s.review_action(db,user,data)
                            elif route=='/api/contact': result=s.contact(db,user,data)
                            elif route=='/api/notification-text': result=s.notification_text(db,user,data)
                            elif route=='/api/business': result=s.apply_business(db,user,data)
                            elif route=='/api/business/restore-preview': result=s.restore_business(db,user,session,data,preview=True)
                            elif route=='/api/business/restore': result=s.restore_business(db,user,session,data)
                            elif route=='/api/exports/authorize':
                                require(user['role']=='admin');s.recent(session)
                                s.audit(db,user['username'],'export_authorized',s.text(data.get('kind','business'),30));result={'ok':True}
                            elif route=='/api/draft':
                                db.execute('SAVEPOINT draft')
                                payload={**data,'version':s.read_state(db)[0],'request_id':secrets.token_hex(16)}
                                s.apply_business(db,user,payload)
                                db.execute('ROLLBACK TO draft');db.execute('RELEASE draft')
                                result={'format':'classic-authorized-draft','operations':data['operations']}
                            else:raise APIError(404,'接口不存在；旧整份状态写入接口已关闭。')
                    db.commit()
                self.json_response(200,result,headers)
            except APIError as e:self.json_response(e.status,{'error':e.message})
            except (KeyError,TypeError,ValueError,StopIteration,OverflowError):self.json_response(400,{'error':'数据格式不正确。'})
            except sqlite3.IntegrityError:self.json_response(409,{'error':'对象已存在或关联数据发生变化，请刷新。'})
            except Exception:self.json_response(500,{'error':'服务器处理失败，本次修改未保存。'})
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    commands=parser.add_subparsers(dest='command',required=True)
    serve=commands.add_parser('serve');serve.add_argument('--port',type=int,default=8790)
    account=commands.add_parser('user');account.add_argument('username');account.add_argument('--role',choices=list(s.ROLES),required=True);account.add_argument('--department',default='');account.add_argument('--name',default='');account.add_argument('--interviewer',action='store_true')
    commands.add_parser('init-db')
    backup=commands.add_parser('backup');backup.add_argument('output')
    args=parser.parse_args();path=Path(args.db).resolve()
    require(Path(args.db).is_absolute() and PUBLIC not in path.parents,'数据库须使用网站目录之外的绝对路径。',400)
    init_db(str(path))
    if args.command=='user':
        value=getpass.getpass('输入新密码（8—128字符，不显示）：')
        require(value==getpass.getpass('再次输入：'),'两次密码不一致。',400)
        set_user(str(path),args.username,args.role,value,args.department,args.name,args.interviewer)
        print('个人账号已创建或重置，旧会话已撤销。')
    elif args.command=='backup':
        output=Path(args.output).resolve();require(PUBLIC not in output.parents and not output.exists(),'备份须使用网站目录外的新文件。',400)
        output.parent.mkdir(parents=True,exist_ok=True)
        with connect(str(path)) as source,contextlib.closing(sqlite3.connect(str(output))) as target:source.backup(target)
        if os.name!='nt':os.chmod(output,0o600)
        print('备份完成。')
    elif args.command=='serve':
        server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(str(path)))
        print(f'{VERSION}: http://127.0.0.1:{args.port}',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:server.server_close()


if __name__=='__main__':main()
