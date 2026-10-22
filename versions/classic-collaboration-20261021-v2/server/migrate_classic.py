"""Copy a verified classic-login database to a NEW private collaboration database.

Never accepts permissions-v1/v2. Shared department accounts stay disabled unless
an operator explicitly maps them to a verified individual in a private JSON file.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import service as s
from errors import require


def migrate(source,target,mapping=None):
    source,target=Path(source).resolve(),Path(target).resolve()
    require(source.is_file() and source!=target and not target.exists(),'需要现有来源库与未存在的新目标库。',400)
    public=Path(__file__).resolve().parent.parent/'dist'
    require(public not in target.parents and public not in source.parents,'数据库不能放在公开网页目录。',400)
    mapping=mapping or {}
    require(isinstance(mapping,dict),'映射文件应为JSON对象。',400)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as old:
        old.row_factory=sqlite3.Row
        old.execute('PRAGMA query_only=ON');old.execute('BEGIN')
        tables={r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require({'users','state','sessions','audit'}<=tables and 'metadata' not in tables,'只接受原版登录版来源库，不能使用权限增强版库。',400)
        require({r[1] for r in old.execute('PRAGMA table_info(users)')}=={'username','role','salt','password'},'来源账号结构不兼容。',400)
        row=old.execute('SELECT version,payload FROM state WHERE id=1').fetchone();require(row is not None,'来源业务状态不存在。',400)
        state=json.loads(row['payload']);s.validate_state(state)
        original_users=old.execute('SELECT * FROM users').fetchall()
        require(set(mapping)<={u['username'] for u in original_users},'映射包含不存在的来源账号。',400)
        identities=[]
        for user in original_users:
            require(user['role']=='总负责人' or user['role'] in s.DEPARTMENTS,'来源存在未知角色。',400)
            require(len(user['salt'])==32 and len(user['password'])==128,'来源密码哈希格式不兼容。',400)
            info=mapping.get(user['username'])
            if user['role']=='总负责人':
                role,dept,active,name,interviewer='admin','',1,user['username'],0
            elif info:
                require(isinstance(info,dict) and info.get('verified_individual') is True,'部门账号映射必须明确核实为个人账号。',400)
                role,dept=info.get('role'),info.get('department')
                require(role in ('lead','head','staff'),'部门旧号不能通过迁移映射变成总负责人。',400)
                s.validate_identity(role,dept)
                name=s.text(info.get('display_name'),80,1);active=1;interviewer=int(bool(info.get('can_interview')))
            else:
                role,dept,active,name,interviewer='staff',user['role'],0,user['username']+'（待核实旧号）',0
            identities.append((user,name,role,dept,active,interviewer))
        target.parent.mkdir(parents=True,exist_ok=True)
        backup=Path(str(target)+'.source-backup.sqlite3')
        require(not backup.exists(),'来源备份目标已存在，请使用新的目标文件名。',400)
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        with closing(sqlite3.connect(backup)) as dest:old.backup(dest)
        # Only after successful source validation and backup reserve the new target.
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        s.init_db(str(target))
        with s.connect(str(target)) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE state SET version=?,payload=? WHERE id=1',(row['version'],s.dumps(state)))
            for user,name,role,dept,active,interviewer in identities:
                db.execute('INSERT INTO users(username,display_name,role,department,salt,password,active,can_interview,created) VALUES(?,?,?,?,?,?,?,?,?)',
                           (user['username'],name,role,dept,user['salt'],user['password'],active,interviewer,s.now()))
            s.audit(db,'system','classic_copy_migration','source_sessions_not_imported')
        report={'source_type':'classic-login','target_version':s.VERSION,'source_backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),
                'counts':{k:len(state[k]) for k in ('candidates','schedules','records','matches')},'users':len(identities),
                'disabled_unmapped':sum(not row[4] for row in identities),'sessions_imported':0,'tasks_created':0,
                'notes':['旧部门记录保留为部门记录，不伪造个人作者。','旧部门共享号未映射则停用。','来源备份包含私有账号资料，禁止上传公开仓库。']}
        report_path=Path(str(target)+'.migration-report.json')
        with report_path.open('x',encoding='utf-8') as out:json.dump(report,out,ensure_ascii=False,indent=2)
        if os.name!='nt':os.chmod(report_path,0o600)
        return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--target',required=True);parser.add_argument('--mapping')
    args=parser.parse_args()
    mapping=json.loads(Path(args.mapping).read_text(encoding='utf-8')) if args.mapping else None
    print(json.dumps(migrate(args.source,args.target,mapping),ensure_ascii=False,indent=2))
