#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then printf '%s\n' '请使用sudo运行独立安装器。' >&2; exit 1; fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
release_dir=/opt/recruitment/releases/classic-collaboration-20261021-v2
data_dir=/var/lib/recruitment/classic-collaboration
backup_dir=/var/backups/recruitment/classic-collaboration
if [ -e "$release_dir" ] || [ -e /etc/systemd/system/recruitment-classic-collaboration.service ] || [ -e "$data_dir/recruitment.sqlite3" ]; then
  printf '%s\n' '独立程序、服务或数据库已经存在，停止以避免覆盖。' >&2; exit 1
fi
/usr/bin/python3 -c 'import sys,sqlite3,hashlib; assert sys.version_info >= (3,10) and hasattr(hashlib,"scrypt")'
if ! id recruitment >/dev/null 2>&1; then useradd --system --user-group --home-dir /var/lib/recruitment --shell /usr/sbin/nologin recruitment; fi
install -d -m 755 "$release_dir/server" "$release_dir/dist"
install -d -m 700 -o recruitment -g recruitment "$data_dir" "$backup_dir"
for file in app.py service.py errors.py classic.py backup.py migrate_classic.py; do install -m 644 "$source_dir/server/$file" "$release_dir/server/"; done
for file in index.html login.js team.js team.css xlsx.full.min.js student-union-recruitment-template.xlsx; do install -m 644 "$source_dir/dist/$file" "$release_dir/dist/"; done
for unit in recruitment-classic-collaboration.service recruitment-classic-collaboration-backup.service recruitment-classic-collaboration-backup.timer; do install -m 644 "$source_dir/server/$unit" /etc/systemd/system/; done
systemctl daemon-reload
systemctl enable --now recruitment-classic-collaboration.service
systemctl enable --now recruitment-classic-collaboration-backup.timer
printf '%s\n' '已独立启动127.0.0.1:8790空库服务。创建个人账号并验收后，再决定公网入口。'
printf '%s\n' '旧服务、旧数据库、Nginx和证书未切换。此安装器不是生产迁移命令。'
