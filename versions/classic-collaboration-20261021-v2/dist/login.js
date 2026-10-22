/* Authenticated adapter for the original recruitment interface. */
(() => {
  'use strict';
  let version=0, revision='', scopeRevision='', csrf='', baseline=null, pending=null, saving=false, blocked=false, locked=false, polling=false, epoch=0;
  const clone=v=>JSON.parse(JSON.stringify(v)),equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const api=window.RecruitSync={user:null,permissions:{},save,request,load,expire,restore:async next=>{
    if(pending||saving||blocked)throw new Error('请先完成或放弃当前未同步修改，再恢复备份。');
    const preview=await request('business/restore-preview',{state:next});
    const result=await window.RecruitTeam.secureRequest('business/restore',{state:next,version,hash:preview.hash});
    accept(result);status('业务备份已恢复，相关任务需重新确认');
  }};
  const gate=document.createElement('div');gate.className='account-gate';
  gate.innerHTML=`<form class="account-card" id="account-form"><div class="account-mark">招</div><h1>学生会招新协同平台</h1><p>使用个人账号登录，继续招新与协作工作。</p><label for="account-username">账号</label><input id="account-username" name="username" autocomplete="username" maxlength="40" required><label for="account-password">密码</label><div class="account-password-row"><input id="account-password" name="password" type="password" autocomplete="current-password" required><button class="btn" type="button" id="account-toggle">显示</button></div><button class="btn primary account-submit" id="account-submit">登录</button><div id="account-error" class="account-error" role="alert"></div><p class="account-hint">账号密码登录。一个账号保留一个有效登录；新登录成功会使旧登录失效。</p></form>`;
  document.body.append(gate);
  const bar=document.createElement('div');bar.className='sync-bar';bar.hidden=true;
  bar.innerHTML='<span data-account></span><span data-status role="status">正在连接…</span><button class="btn" data-retry hidden>重试保存</button><button class="btn" data-draft hidden>导出授权草稿</button><button class="btn" data-refresh hidden>重读服务器</button><button class="btn" data-password>修改密码</button><button class="btn" data-logout>退出登录</button>';
  document.querySelector('.topbar').after(bar);
  const form=gate.querySelector('form'),error=gate.querySelector('#account-error'),password=gate.querySelector('#account-password');
  try{error.textContent=sessionStorage.getItem('classic-team-notice')||'';sessionStorage.removeItem('classic-team-notice');}catch(_){}
  gate.querySelector('#account-toggle').onclick=e=>{password.type=password.type==='password'?'text':'password';e.target.textContent=password.type==='password'?'显示':'隐藏';};
  function status(message,failed=false){bar.querySelector('[data-status]').textContent=message;bar.dataset.error=String(failed);for(const name of ['retry','draft','refresh'])bar.querySelector(`[data-${name}]`).hidden=!failed;}
  function expire(message='登录已失效，请重新登录。'){
    if(locked)return;locked=true;epoch++;pending=null;baseline=null;api.user=null;csrf='';
    document.documentElement.classList.add('auth-pending');window.RecruitTeam?.clear();
    try{sessionStorage.setItem('classic-team-notice',message);}catch(_){}
    location.reload();
  }
  async function request(path,data){
    const requestEpoch=epoch;let response;
    try{response=await fetch('/api/'+path,{credentials:'same-origin',cache:'no-store',method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});}
    catch(_){throw new Error('暂时无法连接服务器，请检查网络后重试。');}
    let result;try{result=await response.json();}catch(_){throw new Error('服务器返回异常。');}
    if(requestEpoch!==epoch)throw new Error('当前登录范围已变化，请重试。');
    if(!response.ok){if(response.status===401&&api.user&&path!=='login')expire(result.error);const e=new Error(result.error||'请求失败');e.status=response.status;throw e;}
    return result;
  }
  function accept(result){
    version=result.version;revision=result.revision;scopeRevision=result.scope_revision;api.user=result.user;api.permissions=result.permissions;baseline=clone(result.state);
    window.recruitmentApp.setState(result.state,api.user);window.RecruitTeam?.decorate();
    bar.querySelector('[data-account]').textContent=`${api.user.display_name} · ${api.user.role_label}${api.user.department?' · '+api.user.department:''}`;
  }
  async function load(){const result=await request('bootstrap');if(locked)return;accept(result);blocked=false;status('已同步');}
  async function enter(session){
    api.user=session.user;csrf=session.csrf;
    if(api.user.must_change){
      const initialPassword=password.value;
      const box=document.createElement('form');box.className='account-card';
      box.innerHTML='<h1>设置正式密码</h1><p>首次登录需设置自己的密码，8—128字符。</p><label>当前临时密码<input name="old" type="password" autocomplete="current-password" required></label><label>新密码<input name="next" type="password" autocomplete="new-password" required></label><label>再次输入<input name="confirm" type="password" autocomplete="new-password" required></label><button class="btn primary account-submit">设置并重新登录</button><div class="account-error" role="alert"></div>';
      box.old.value=initialPassword;password.value='';gate.replaceChildren(box);
      box.onsubmit=async e=>{e.preventDefault();try{
        if(box.next.value!==box.confirm.value)throw new Error('两次密码不一致。');
        await request('me/password',{old_password:box.old.value,password:box.next.value});expire('正式密码设置成功，请重新登录。');
      }catch(e){box.querySelector('.account-error').textContent=e.message;}};
      return;
    }
    await load();if(locked)return;
    password.value='';gate.hidden=true;bar.hidden=false;
    document.querySelector('.brand small').textContent='原版界面 · 个人协作';
    document.querySelector('.side-note').textContent='共享数据由服务器保存。本人身份与查看部门分开，任务按授权范围协作。';
    document.querySelectorAll('.notice:not(#team-home-count) .btn,#load-sample').forEach(el=>el.hidden=true);
    document.querySelector('.notice:not(#team-home-count) > div').textContent='原版协作版 · 保留原页面和业务操作，账号、任务与权限由服务器管理。';
    document.documentElement.classList.remove('auth-pending');
    await window.RecruitTeam?.refresh();
  }
  function diff(before,after){
    const operations=[];
    for(const [key,kind,id] of [['candidates','candidate',x=>x.id],['schedules','schedule',x=>x.id],['records','record',x=>x.candidate+'|'+x.dept]]){
      const a=new Map(before[key].map(x=>[id(x),x])),b=new Map(after[key].map(x=>[id(x),x]));
      const clean=x=>{const v=clone(x);if(kind==='candidate')delete v.status;return v;};
      for(const [key,item] of a)if(!b.has(key))operations.push({kind,action:'delete',item:clone(item)});
      for(const [key,item] of b)if(!a.has(key)||!equal(clean(a.get(key)),clean(item)))operations.push({kind,action:'upsert',item:clone(item)});
    }
    for(const key of ['rankings','departmentAvailability','interviewConfig'])for(const dept of Object.keys(after[key]))if(!equal(before[key][dept],after[key][dept]))operations.push({kind:key==='rankings'?'ranking':key,action:'upsert',key:dept,item:clone(after[key][dept])});
    for(const key of ['quotas','rules','timeline','notificationTemplates'])if(!equal(before[key],after[key]))operations.push({kind:key,action:'upsert',item:clone(after[key])});
    if(!equal(before.matches,after.matches))operations.push({kind:'matches',action:'upsert',item:clone(after.matches)});
    return operations;
  }
  function save(next){if(locked)return;pending=clone(next);if(api.user&&!blocked)flush();}
  async function flush(){
    if(saving||blocked||!pending||locked)return;
    const snapshot=pending;pending=null;saving=true;status('正在保存…');
    try{
      const operations=diff(baseline,snapshot);
      if(!operations.length){status('已同步');return;}
      const result=await request('business',{version,operations,request_id:crypto.randomUUID()});
      if(locked)return;
      if(pending){version=result.version;revision=result.revision;scopeRevision=result.scope_revision;baseline=clone(result.state);}else accept(result);
      status('已同步');
    }catch(e){if(!locked){pending=pending||snapshot;blocked=true;status(e.message+'（编辑暂留当前页面）',true);}}
    finally{saving=false;if(pending&&!blocked&&!locked)flush();}
  }
  form.onsubmit=async e=>{e.preventDefault();const button=gate.querySelector('#account-submit');button.disabled=true;error.textContent='';try{await enter(await request('login',{username:form.username.value.trim(),password:password.value}));}catch(e){if(!locked)error.textContent=e.message;}finally{button.disabled=false;}};
  bar.querySelector('[data-retry]').onclick=()=>{blocked=false;flush();};
  bar.querySelector('[data-refresh]').onclick=async()=>{if(pending&&!confirm('放弃当前未同步修改并重读服务器数据？'))return;try{pending=null;await load();}catch(e){status(e.message,true);}};
  bar.querySelector('[data-draft]').onclick=async()=>{try{
    const result=await request('draft',{operations:diff(baseline,pending||window.recruitmentApp.getState())});
    const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='招新协作_授权编辑草稿.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){status(e.message,true);}};
  bar.querySelector('[data-password]').onclick=()=>window.RecruitTeam.passwordForm();
  bar.querySelector('[data-logout]').onclick=async()=>{if((pending||saving)&&!confirm('放弃未同步编辑并退出？'))return;try{await request('logout',{});expire('已退出登录。');}catch(e){status(e.message,true);}};
  window.addEventListener('beforeunload',e=>{if(!locked&&(pending||saving)){e.preventDefault();e.returnValue='';}});
  async function poll(force=false){
    if(!api.user||locked||api.user.must_change||polling||document.hidden)return;polling=true;let checked=false;
    try{
      const session=await request('session');checked=true;
      if(session.scope_revision!==scopeRevision){
        document.documentElement.classList.add('auth-pending');epoch++;pending=null;window.RecruitTeam?.clear();location.reload();return;
      }
      const editing=pending||saving||blocked||document.querySelector('.modal-backdrop.open,.detail-panel.open')||document.activeElement?.matches('input,textarea,select');
      if(!editing&&(session.revision!==revision||session.version!==version)){
        accept(await request('bootstrap'));await window.RecruitTeam?.refresh();status('已同步协作更新');
      }
    }catch(e){if(!locked)status(e.message,true);}finally{polling=false;if(force&&checked&&!locked)document.documentElement.classList.remove('auth-pending');}
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&api.user&&!api.user.must_change){document.documentElement.classList.add('auth-pending');poll(true);}});
  setInterval(()=>poll(document.documentElement.classList.contains('auth-pending')),6000);
  const start=()=>request('session').then(enter).catch(e=>{if(!locked&&e.status!==401)error.textContent=e.message;});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
