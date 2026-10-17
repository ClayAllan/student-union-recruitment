"""Prepare the browser-local demo for GitHub Pages, without runtime data."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil


def build(output):
    root = Path(__file__).resolve().parent.parent
    source = root / 'dist'
    html = (source / 'index.html').read_text(encoding='utf-8')
    if 'window.RECRUIT_COLLAB=true' in html or 'window.RECRUIT_COLLAB = true' in html:
        raise ValueError('The public demo must use the static frontend.')
    notice = '<b>公开演示 · 20261017 展示基线</b>　内置60名虚构报名者，可自由体验；修改只保存在当前浏览器。请勿导入真实个人信息。'
    html, replaced = re.subn(r'(<div class="notice"><div>).*?(</div><button)', lambda m: m[1] + notice + m[2], html, count=1, flags=re.S)
    if replaced != 1:
        raise ValueError('Demo notice was not found; check the frontend structure.')
    html = html.replace('<title>学生会招新协同平台</title>', '<title>学生会招新协同平台 · 在线演示</title>')
    html = html.replace('recruit-demo-', 'recruit-public-demo-')
    html = html.replace('recruit-theme-accent', 'recruit-public-demo-theme-accent')
    html = html.replace('recruit-color-scheme', 'recruit-public-demo-color-scheme')
    html = html.replace('lockAfterDeadline: true', 'lockAfterDeadline: false')
    today = datetime.now(timezone(timedelta(hours=8))).date()
    dates = {'2026-10-08': today + timedelta(days=2), '2026-10-09': today + timedelta(days=3),
             '2026-10-10': today + timedelta(days=4), '2026-10-16': today + timedelta(days=7)}
    html = re.sub('|'.join(map(re.escape, dates)), lambda m: dates[m[0]].isoformat(), html)
    destination = Path(output).resolve()
    if destination == root or destination == source or root in destination.parents and destination.name != '_site':
        raise ValueError('Choose a dedicated _site folder or a folder outside the source checkout.')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('Use an empty output folder to avoid publishing unrelated files.')
    (destination / 'index.html').write_text(html, encoding='utf-8')
    for name in ['xlsx.full.min.js', 'student-union-recruitment-template.xlsx']:
        shutil.copyfile(source / name, destination / name)
    (destination / '.nojekyll').touch()
    print(f'Public demo prepared: {destination}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    build(parser.parse_args().output)
