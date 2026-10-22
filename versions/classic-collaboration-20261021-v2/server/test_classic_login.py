"""Migration and classic-business compatibility regressions."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import service as s
from errors import APIError
from migrate_classic import migrate
from test_service import CollaborationTest as Base


class ClassicCompatibilityTest(Base):
    # Inherit fixture helpers, without duplicating the full base test suite.
    def test_copy_migration_retains_state_disables_shared_accounts(self):
        old=Path(self.tmp.name)/'classic.sqlite3';target=Path(self.tmp.name)/'copied.sqlite3'
        with s.connect(self.path) as db:
            _,state=s.read_state(db)
            accounts=[dict(r) for r in db.execute("SELECT * FROM users WHERE username IN ('manager','lead')")]
        with closing(sqlite3.connect(old)) as db:
            db.executescript('CREATE TABLE users(username TEXT PRIMARY KEY,role TEXT,salt TEXT,password TEXT);CREATE TABLE state(id INTEGER,version INTEGER,payload TEXT);CREATE TABLE sessions(token_hash TEXT);CREATE TABLE audit(id INTEGER);')
            db.execute('INSERT INTO state VALUES(1,4,?)',(s.dumps(state),))
            for u in accounts:db.execute('INSERT INTO users VALUES(?,?,?,?)',(u['username'],'总负责人' if u['role']=='admin' else '权益部',u['salt'],u['password']))
            db.execute("INSERT INTO sessions VALUES('synthetic-old-session')");db.commit()
        before=hashlib.sha256(old.read_bytes()).hexdigest();report=migrate(old,target)
        self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(),before)
        self.assertEqual(report['disabled_unmapped'],1);self.assertEqual(report['sessions_imported'],0)
        with s.connect(str(target)) as db:
            self.assertEqual(s.read_state(db),(4,state));self.assertFalse(db.execute("SELECT active FROM users WHERE username='lead'").fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
            self.assertTrue(s.verify(db.execute("SELECT * FROM users WHERE username='manager'").fetchone(),self.password))
        with self.assertRaises(APIError):migrate(old,target)

    def test_migration_rejects_enhanced_before_creating_target(self):
        target=Path(self.tmp.name)/'should-not-exist.sqlite3'
        before=Path(self.path).read_bytes()
        with self.assertRaises(APIError):migrate(self.path,target)
        self.assertFalse(target.exists());self.assertEqual(Path(self.path).read_bytes(),before)

    def test_edit_does_not_reorder_candidates_or_change_match(self):
        state=self.manager.request('/api/bootstrap')[1]['state'];before=copy.deepcopy(state)
        before['rules']['autoTransfer']=True
        note=copy.deepcopy(state['candidates'][0]);note['note']='仅修改虚构备注'
        self.assertEqual(self.business(self.manager,[{'kind':'candidate','action':'upsert','item':note}])[0],200)
        after=self.manager.request('/api/bootstrap')[1]['state'];after['rules']['autoTransfer']=True
        self.assertEqual([c['id'] for c in before['candidates']],[c['id'] for c in after['candidates']])
        self.assertEqual(s.generate_matches(before),s.generate_matches(after))

    def test_restore_and_export_check_admin_and_preserve_accounts(self):
        state=self.manager.request('/api/bootstrap')[1]['state']
        preview=self.manager.request('/api/business/restore-preview',{'state':state})[1]
        self.assertEqual(self.client('lead').request('/api/business/restore-preview',{'state':state})[0],403)
        payload={'state':state,'version':0,'hash':preview['hash']}
        self.assertEqual(self.manager.request('/api/business/restore',payload)[0],428)
        self.assertEqual(self.manager.request('/api/exports/authorize',{'kind':'business-json'})[0],428)
        self.reauth();self.assertEqual(self.manager.request('/api/business/restore',payload)[0],200)
        self.assertEqual(self.manager.request('/api/exports/authorize',{'kind':'business-json'})[0],200)
        with s.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],6)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM restore_history').fetchone()[0],1)


# Reuse the setup/helper implementation but avoid inherited test discovery.
for _name in dir(Base):
    if _name.startswith('test_') and _name not in ClassicCompatibilityTest.__dict__:
        setattr(ClassicCompatibilityTest,_name,None)
del Base
