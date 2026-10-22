"""Read-only deployment inventory. Prints counts and configuration paths, never records or secrets."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return -1, ''


def database_summary(path):
    result = {'path': str(path), 'exists': path.is_file()}
    if not result['exists']:
        return result
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'metadata' in tables:
                result['metadata'] = dict(db.execute("SELECT key,value FROM metadata WHERE key IN ('schema','application')"))
            if 'users' in tables:
                result['account_count'] = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            if 'objects' in tables:
                result['live_object_count'] = db.execute('SELECT COUNT(*) FROM objects WHERE deleted=0').fetchone()[0]
                result['live_counts'] = {}
                for kind in ('candidate', 'schedule', 'review', 'conclusion', 'ranking', 'notice_task', 'interview_group'):
                    result['live_counts'][kind] = db.execute('SELECT COUNT(*) FROM objects WHERE kind=? AND deleted=0', (kind,)).fetchone()[0]
            elif 'state' in tables:
                row = db.execute('SELECT payload FROM state WHERE id=1').fetchone()
                if row:
                    state = json.loads(row[0])
                    result['business_counts'] = {key: len(state.get(key, [])) for key in ('candidates', 'schedules', 'records', 'matches')}
        finally:
            db.close()
    except (sqlite3.Error, ValueError, TypeError):
        result['inspection'] = 'unrecognized_or_unreadable_database'
    return result


def nginx_summary(config):
    current = ''
    result = []
    for line in config.splitlines():
        match = re.match(r'# configuration file ([^:]+):', line)
        if match:
            current = match[1]
            continue
        stripped = line.strip()
        if stripped.startswith('proxy_pass '):
            target = urlparse(stripped[len('proxy_pass '):].split(';', 1)[0].strip())
            try:
                port = target.port
            except ValueError:
                port = None
            result.append({'file': current, 'directive': 'proxy_pass', 'scheme': target.scheme,
                           'host': target.hostname, 'port': port})
        elif stripped.startswith(('listen ', 'server_name ', 'location ')):
            # These known directives carry routing, never credentials or certificate material.
            result.append({'file': current, 'routing': stripped.split('#', 1)[0].strip()})
    return result


def main():
    report = {'purpose': 'read_only_preflight', 'python': sys.version.split()[0],
              'scrypt_available': hasattr(hashlib, 'scrypt'), 'services': {}}
    for unit in ('recruitment.service', 'recruitment-classic-collaboration.service', 'nginx.service'):
        _, output = command('systemctl', 'show', unit, '--property=ActiveState,FragmentPath,DropInPaths')
        report['services'][unit] = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    _, start = command('systemctl', 'show', 'recruitment.service', '--property=ExecStart', '--value')
    match = re.search(r'--db\s+(?:"([^"]+)"|([^\s;]+))', start)
    paths = {Path('/var/lib/recruitment/recruitment-v2.sqlite3'),
             Path('/var/lib/recruitment/classic-collaboration/recruitment.sqlite3')}
    if match:
        candidate = Path(match.group(1) or match.group(2))
        if candidate.is_absolute() and candidate.suffix in ('.db', '.sqlite', '.sqlite3'):
            paths.add(candidate)
    report['databases'] = [database_summary(path) for path in sorted(paths)]
    status, config = command('nginx', '-T')
    report['nginx_config_valid'] = status == 0
    report['nginx_routing'] = nginx_summary(config)
    report['nginx_site_links'] = []
    directory = Path('/etc/nginx/sites-enabled')
    if directory.is_dir():
        report['nginx_site_links'] = [{'path': str(path), 'target': str(path.resolve())} for path in sorted(directory.iterdir())]
    report['local_health'] = {}
    opener = build_opener(ProxyHandler({}))
    for port in (8787, 8790):
        try:
            with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=4) as response:
                value = json.load(response)
            report['local_health'][str(port)] = {key: value[key] for key in ('ok', 'schema', 'version', 'application') if key in value}
        except (OSError, ValueError):
            report['local_health'][str(port)] = {'available': False}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
