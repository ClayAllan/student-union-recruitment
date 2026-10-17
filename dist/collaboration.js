/* Internal accounts and shared-state synchronization. No candidate data in localStorage. */
(() => {
  'use strict';
  let version = 0, csrf = '', pending = null, saving = false, blocked = false, polling = false;
  const api = window.RecruitSync = { user: null, save };
  const css = document.createElement('style');
  css.textContent = `
    [hidden]{display:none!important}
    .account-gate{position:fixed;inset:0;z-index:10000;background:var(--paper);display:grid;place-items:center;padding:24px;overflow:auto}
    .account-card{width:min(100%,420px);padding:32px;border:1px solid var(--line);border-radius:18px;background:var(--panel);box-shadow:var(--shadow-lg)}
    .account-card h1{font-size:24px;margin:0 0 8px}.account-card p{color:var(--muted);font-size:14px;line-height:1.7;margin:0 0 24px}
    .account-card label{display:block;margin:16px 0 6px;font-size:13px;font-weight:600}.account-card input{width:100%;box-sizing:border-box;border:1px solid var(--line);border-radius:8px;padding:12px;background:var(--panel);color:var(--ink);font:inherit}
    .account-card button{width:100%;margin-top:20px}.account-error{color:#dc2626;font-size:13px;line-height:1.6;min-height:20px;margin-top:12px}
    .sync-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 20px;padding:12px 16px;border:1px solid var(--line);border-radius:10px;background:var(--panel);font-size:13px;color:var(--muted)}
    .sync-bar [data-status]{flex:1;min-width:160px}.sync-bar button{font-size:12px}.sync-bar[data-error=true]{border-color:#f59e0b}
    .account-gate[hidden]{display:none!important}
  `;
  document.head.append(css);
  const gate = document.createElement('div');
  gate.className = 'account-gate';
  gate.innerHTML = `<form class="account-card" id="account-form"><h1>学生会招新协同平台</h1><p>负责人内部协作<br>请使用分配给你的账号登录。</p><label for="account-username">账号</label><input id="account-username" name="username" autocomplete="username" required maxlength="40"><label for="account-password">密码</label><input id="account-password" name="password" type="password" autocomplete="current-password" required maxlength="128"><button class="btn primary" type="submit">登录</button><div class="account-error" id="account-error" role="alert"></div></form>`;
  document.body.append(gate);
  const form = gate.querySelector('form');
  const error = gate.querySelector('#account-error');
  const bar = document.createElement('div');
  bar.className = 'sync-bar';
  bar.hidden = true;
  bar.innerHTML = `<span data-account></span><span data-status role="status" aria-live="polite">正在连接…</span><button class="btn" data-retry hidden>重试保存</button><button class="btn" data-draft hidden>导出未同步草稿</button><button class="btn" data-refresh hidden>刷新服务器数据</button><button class="btn" data-logout>退出登录</button>`;
  document.querySelector('.topbar').after(bar);

  function status(text, failed = false, conflict = false) {
    bar.dataset.error = String(failed);
    bar.querySelector('[data-status]').textContent = text;
    bar.querySelector('[data-retry]').hidden = !failed || conflict;
    bar.querySelector('[data-draft]').hidden = !failed;
    bar.querySelector('[data-refresh]').hidden = !failed;
  }

  async function request(path, data) {
    const response = await fetch('/api/' + path, {
      credentials: 'same-origin', cache: 'no-store',
      method: data ? 'POST' : 'GET',
      headers: data ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } : {},
      body: data ? JSON.stringify(data) : undefined
    });
    let result;
    try { result = await response.json(); }
    catch { throw new Error('服务器返回异常，请检查协作服务是否运行。'); }
    if (!response.ok) {
      const exception = new Error(result.error || '请求失败');
      exception.status = response.status;
      throw exception;
    }
    return result;
  }

  async function enter(session) {
    api.user = session.user;
    csrf = session.csrf;
    await load();
    gate.hidden = true;
    bar.hidden = false;
    bar.querySelector('[data-account]').textContent = `${api.user.username} · ${api.user.role}`;
    document.querySelector('.brand small').textContent = '负责人内部协作';
    const note = document.querySelector('.side-note');
    note.replaceChildren();
    const title = document.createElement('b'); title.textContent = '共享数据';
    note.append(title, document.createElement('br'), '报名、排期与评价保存在服务器，其他负责人保存后会自动同步。');
    document.querySelectorAll('.notice .btn,#load-sample').forEach(el => el.hidden = true);
    const notice = document.querySelector('.notice > div');
    notice.textContent = '负责人协作版 · 数据由服务器共享保存，按账号限制操作权限。';
    gate.querySelector('#account-password').value = '';
  }

  async function load() {
    const result = await request('state');
    version = result.version;
    window.recruitmentApp.setState(result.state, api.user);
    blocked = false;
    status('已同步');
  }

  function save(next) {
    pending = next;
    if (!api.user) { status('请先登录，当前修改尚未同步。', true); return; }
    if (!blocked) flush();
  }

  async function flush() {
    if (saving || blocked || !pending) return;
    const snapshot = pending;
    pending = null;
    saving = true;
    status('正在保存…');
    try {
      const result = await request('state', { version, state: snapshot });
      version = result.version;
      status('已同步');
    } catch (exception) {
      pending = pending || snapshot;
      blocked = true;
      status(exception.message + '（修改保留在当前页面）', true, exception.status === 409);
    } finally {
      saving = false;
      if (pending && !blocked) flush();
    }
  }

  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true; error.textContent = '';
    try {
      const session = await request('login', { username: form.username.value.trim(), password: form.password.value });
      await enter(session);
    } catch (exception) { error.textContent = exception.message; }
    finally { button.disabled = false; }
  };

  bar.querySelector('[data-retry]').onclick = () => { blocked = false; flush(); };
  bar.querySelector('[data-refresh]').onclick = async () => {
    if (pending && !confirm('当前页面有未同步修改。请先导出草稿。确定放弃这些修改并读取服务器数据吗？')) return;
    try { await load(); pending = null; }
    catch (exception) { status(exception.message, true); }
  };
  bar.querySelector('[data-draft]').onclick = () => {
    const blob = new Blob([JSON.stringify({ format: 'recruit-backup', version: 3, exportedAt: new Date().toISOString(), state: pending || window.recruitmentApp.getState() }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = '招新未同步草稿.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  bar.querySelector('[data-logout]').onclick = async () => {
    if ((pending || saving) && !confirm('还有未同步修改。请先保存或导出草稿，确定退出吗？')) return;
    try { await request('logout', {}); location.reload(); }
    catch (exception) { status(exception.message, true); }
  };

  window.addEventListener('beforeunload', event => {
    if (pending || saving) { event.preventDefault(); event.returnValue = ''; }
  });

  setInterval(async () => {
    if (!api.user || pending || saving || blocked || polling || document.hidden) return;
    if (document.querySelector('.modal-backdrop.open,.detail-panel.open') || document.activeElement?.matches('input,textarea,select')) return;
    polling = true;
    try {
      const result = await request('state');
      if (pending || saving || blocked) return;
      if (result.version !== version) {
        version = result.version;
        window.recruitmentApp.setState(result.state, api.user);
        status('已同步其他负责人的更新');
      }
    } catch (exception) { status(exception.message, true); }
    finally { polling = false; }
  }, 6000);

  request('session').then(enter).catch(exception => {
    if (exception.status !== 401) error.textContent = exception.message;
  });
})();
