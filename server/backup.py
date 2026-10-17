"""Consistent SQLite backup, retaining 14 days of local backup files."""
import datetime
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing
import app

directory = Path('/var/backups/recruitment')
directory.mkdir(parents=True, exist_ok=True)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
output = directory / ('recruitment-' + stamp + '.sqlite3')
with app.connect('/var/lib/recruitment/recruitment.sqlite3') as source, closing(sqlite3.connect(str(output))) as target:
    source.backup(target)
os.chmod(output, 0o600)
for item in directory.glob('recruitment-*.sqlite3'):
    if item.is_file() and not item.is_symlink() and item.stat().st_mtime < time.time() - 14 * 86400:
        item.unlink()
print('Daily recruitment database backup completed.')
