#!/usr/bin/env bash
# Run from an extracted, reviewed release: sudo bash server/install.sh
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  printf '%s\n' '请使用 sudo bash server/install.sh 执行。' >&2
  exit 1
fi
release_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
/usr/bin/python3 -c 'import sqlite3, hashlib; assert hasattr(hashlib, "scrypt")'
if ! id recruitment >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/recruitment --shell /usr/sbin/nologin recruitment
fi
install -d -m 755 /opt/recruitment /opt/recruitment/server /opt/recruitment/dist
install -d -m 700 -o recruitment -g recruitment /var/lib/recruitment /var/backups/recruitment
install -m 644 "$release_dir/server/app.py" "$release_dir/server/backup.py" /opt/recruitment/server/
install -m 644 "$release_dir/dist/index.html" "$release_dir/dist/collaboration.js" "$release_dir/dist/xlsx.full.min.js" "$release_dir/dist/student-union-recruitment-template.xlsx" /opt/recruitment/dist/
install -m 644 "$release_dir/server/recruitment.service" "$release_dir/server/recruitment-backup.service" "$release_dir/server/recruitment-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now recruitment.service
systemctl enable --now recruitment-backup.timer
printf '%s\n' '协作服务已安装，仅监听本机8787端口。接下来创建负责人账号，再配置域名与HTTPS。'
printf '%s\n' '现有Nginx网站配置未自动切换。'
