"""Switch the verified existing IP HTTPS site; preserve old services and private data."""
from contextlib import closing
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import time

HOST = '81.70.71.131'
VERSION = '20261021-classic-collaboration-v2'
CONFIG = Path('/etc/nginx/sites-available/recruitment-ip')
DATABASE = Path('/var/lib/recruitment/classic-collaboration/recruitment.sqlite3')
OLD_DATABASES = [Path('/var/lib/recruitment/recruitment.sqlite3'), Path('/var/lib/recruitment/recruitment-v2.sqlite3')]
BACKUPS = Path('/var/backups/recruitment')
OLD_UNIT = Path('/etc/systemd/system/recruitment.service')
OLD_DROPINS = Path('/etc/systemd/system/recruitment.service.d')
OLD_ROUTE = b'proxy_pass http://127.0.0.1:8787;'
NEW_ROUTE = b'proxy_pass http://127.0.0.1:8790;'


class DeploymentError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise DeploymentError(message)


def run(*args):
    return subprocess.run(args, check=True, timeout=30, capture_output=True, text=True).stdout


def read_db(path):
    require(path.is_file(), 'Required database is missing; nothing switched.')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.execute('PRAGMA query_only=ON')
    return db


def checkpoint():
    with closing(read_db(DATABASE)) as db:
        db.execute('BEGIN')
        metadata = dict(db.execute('SELECT key,value FROM metadata'))
        require(metadata.get('application') == 'classic-collaboration' and metadata.get('schema') == '2', 'New database identity mismatch.')
        require(db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND must_change=0").fetchone()[0] > 0, 'Create the production administrator before switching.')
        return db.execute('SELECT COALESCE(MAX(id),0) FROM audit').fetchone()[0]


def backup_database(source, target):
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(read_db(source)) as src, closing(sqlite3.connect(target)) as dest:
        src.backup(dest)
        require(dest.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Database backup integrity check failed.')


def atomic_config(content):
    stat = CONFIG.stat()
    fd, name = tempfile.mkstemp(prefix='.recruitment-switch-', dir=CONFIG.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, stat.st_mode & 0o777)
        os.chown(name, stat.st_uid, stat.st_gid)
        os.replace(name, CONFIG)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def check_health(value):
    require(value.get('ok') is True and value.get('version') == VERSION and value.get('application') == 'classic-collaboration'
            and str(value.get('schema')) == '2' and value.get('authentication') == 'password' and value.get('max_sessions') == 1,
            'Expected new backend health identity was not returned.')
    return value


def backend_health():
    connection = http.client.HTTPConnection('127.0.0.1', 8790, timeout=5)
    try:
        connection.request('GET', '/api/health')
        reply = connection.getresponse()
        require(reply.status == 200, 'New backend health is unavailable.')
        return check_health(json.loads(reply.read()))
    finally:
        connection.close()


def https_health():
    # Connect locally but verify the actual public certificate identity, SNI and Host.
    context = ssl.create_default_context()
    with socket.create_connection(('127.0.0.1', 443), timeout=5) as plain:
        with context.wrap_socket(plain, server_hostname=HOST) as secured:
            secured.sendall(f'GET /api/health HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n'.encode('ascii'))
            reply = http.client.HTTPResponse(secured)
            reply.begin()
            require(reply.status == 200, 'HTTPS site health returned an error.')
            return check_health(json.loads(reply.read()))


def switch():
    require(getattr(os, 'geteuid', lambda: -1)() == 0, 'Run with sudo python3 -B switch_https.py.')
    os.umask(0o077)
    backend_health()
    before_audit = checkpoint()
    original = CONFIG.read_bytes()
    require(not CONFIG.is_symlink(), 'Unexpected symlink at site configuration; stop for inspection.')
    require(original.count(OLD_ROUTE) == 1 and original.count(NEW_ROUTE) == 0,
            'Site route differs from the inspected config, or is already switched. Stop and report; do not repeat installation.')
    run('nginx', '-t')
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / ('classic-cutover-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    print('PRIVATE_BACKUP=' + str(backup), flush=True)
    shutil.copy2(CONFIG, backup / 'recruitment-ip.before.conf')
    for index, path in enumerate([*OLD_DATABASES, DATABASE]):
        backup_database(path, backup / f'database-{index}-{path.name}')
    shutil.copy2(OLD_UNIT, backup / 'recruitment.service')
    shutil.copytree(OLD_DROPINS, backup / 'recruitment.service.d')
    startup = run('systemctl', 'show', 'recruitment.service', '--property=ExecStart', '--value')
    key_argument = re.search(r'--key\s+(?:"([^"]+)"|([^\s;]+))', startup)
    require(key_argument is not None, 'Cannot identify the old service key path; nothing switched.')
    key = Path(key_argument.group(1) or key_argument.group(2))
    require(key.is_absolute() and key.is_file(), 'Old private key file is missing; nothing switched.')
    shutil.copyfile(key, backup / 'legacy-encryption-key.private')
    os.chmod(backup / 'legacy-encryption-key.private', 0o600)
    # Do not change TLS, ACME, any other header or old service configuration.
    replacement = original.replace(OLD_ROUTE, NEW_ROUTE, 1)
    require(CONFIG.read_bytes() == original, 'Nginx config changed during backup; nothing switched.')
    try:
        atomic_config(replacement)
        run('nginx', '-t')
        run('systemctl', 'reload', 'nginx')
        healthy = None
        for attempt in range(5):
            try:
                healthy = https_health()
                break
            except (OSError, ValueError, http.client.HTTPException, DeploymentError):
                if attempt == 4:
                    raise
                time.sleep(1)
    except Exception:
        try:
            safe = checkpoint() == before_audit
        except Exception:
            safe = False
        if not safe:
            raise DeploymentError('SWITCH_CHECK_FAILED_NEW_ACTIVITY: kept current config to avoid silently returning users to an old database. Report this output; do not restore databases.') from None
        current = CONFIG.read_bytes()
        require(current in (original, replacement), 'Config changed externally; automatic restore stopped. Report this output.')
        if current == replacement:
            atomic_config(original)
        try:
            run('nginx', '-t')
            run('systemctl', 'reload', 'nginx')
        except Exception:
            raise DeploymentError('RESTORE_RELOAD_FAILED: original file restored, but reload needs manual inspection. Report this output.') from None
        raise DeploymentError('SWITCH_CHECK_FAILED: original Nginx config restored and reloaded; databases unchanged. Report this output.') from None
    print('SWITCH_OK', flush=True)
    print(json.dumps(healthy, ensure_ascii=False), flush=True)
    try:
        run('systemctl', 'start', 'recruitment-classic-collaboration-backup.service')
        run('systemctl', 'is-active', 'recruitment-classic-collaboration-backup.timer')
        print('NEW_DATABASE_BACKUP_OK', flush=True)
    except (OSError, subprocess.SubprocessError):
        print('NEW_DATABASE_BACKUP_NEEDS_CHECK: HTTPS is switched; do not rerun this script.', flush=True)
    return backup


if __name__ == '__main__':
    try:
        switch()
    except (DeploymentError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print('STOP: ' + type(error).__name__ + ': ' + (str(error) if isinstance(error, DeploymentError) else 'Operation failed; do not repeat installation. Report this output.'), flush=True)
        raise SystemExit(1)
