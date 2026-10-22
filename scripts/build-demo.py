"""Build the live cloud entry and preserve the original static demo separately."""
import argparse
import importlib.util
from pathlib import Path

ENTRY = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="refresh" content="0;url=https://81.70.71.131/">
<link rel="canonical" href="https://81.70.71.131/">
<title>学生会招新协同平台 · 新版入口</title>
<style>
body{margin:0;padding:32px;background:#f4f7fb;color:#172238;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}
main{max-width:580px;margin:12vh auto;padding:32px;border:1px solid #dfe6f0;border-radius:16px;background:white}
h1{font-size:24px;margin:0 0 12px}p{margin:12px 0}a{color:#2456c9}.primary{display:inline-block;padding:10px 20px;border-radius:8px;background:#2456c9;color:white;text-decoration:none}
</style>
</head>
<body>
<main>
<h1>正在进入新版协作平台</h1>
<p>新版账号登录、任务协作和共享数据由云服务器提供。</p>
<p><a class="primary" href="https://81.70.71.131/" rel="noreferrer">进入新版登录页</a></p>
<p>若未自动跳转，请点击上方按钮。</p>
<p><a href="./demo/">查看原静态演示（虚构数据，无真实登录）</a></p>
</main>
<script>window.location.replace("https://81.70.71.131/");</script>
</body>
</html>
'''


def build(output):
    root = Path(__file__).resolve().parent.parent
    destination = Path(output).resolve()
    if destination == root or destination == root / 'dist' or root in destination.parents and destination.name != '_site':
        raise ValueError('Choose a dedicated _site folder or a folder outside the source checkout.')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('Use an empty output folder to avoid publishing unrelated files.')
    spec = importlib.util.spec_from_file_location('recruitment_static_baseline', Path(__file__).with_name('build-demo-baseline.py'))
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    intermediate = destination / '_site'
    demo = destination / 'demo'
    if intermediate.resolve().parent != destination or demo.resolve().parent != destination:
        raise ValueError('Demo paths must remain inside the verified output directory.')
    baseline.build(intermediate)
    intermediate.rename(demo)
    (destination / 'index.html').write_text(ENTRY, encoding='utf-8')
    (destination / '.nojekyll').touch()
    print(f'Live cloud entry and preserved demo prepared: {destination}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    build(parser.parse_args().output)
