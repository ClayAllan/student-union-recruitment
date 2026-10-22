/* Small account/task views using the classic page's existing components. */
(() => {
  'use strict';
  const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
  const e=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roles={admin:'总负责人',lead:'主要负责人',head:'负责人',staff:'工作人员'};
  const departments=['办公室','公关部','活动部','权益部','实践部','宣媒部'];
  const kinds={interview:'面试任务',notice:'通知任务',general:'通用待办'};
  const states=['待开始','进行中','待审核','已完成','已取消'];
  let tasks=[],reviews=[],members=[],users=[],filter={scope:'mine',kind:'',status:'',search:'',department:''},userFilter={role:'',department:'',search:''};
  const api=()=>window.RecruitSync,me=()=>api()?.user,manage=()=>['admin','lead'].includes(me()?.role);
  const req=(path,data)=>api().request(path,data),app=()=>window.recruitmentApp;
  const currentDept=()=>app()?.getViewDepartment()||me()?.department||departments[0];
  const cName=id=>app().getState().candidates.find(c=>c.id===id)?.name||id;
  const person=name=>members.find(m=>m.username===name)?.display_name||users.find(u=>u.username===name)?.display_name||name;
  const when=value=>value?new Date(value*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未设截止';
  const browserLabel=agent=>{if(!agent)return '';const browser=/Edg\//.test(agent)?'Edge':/Firefox\//.test(agent)?'Firefox':/Chrome\//.test(agent)?'Chrome':/Safari\//.test(agent)?'Safari':'其他浏览器';const system=/Android/.test(agent)?'Android':/iPhone|iPad/.test(agent)?'iOS':/Windows/.test(agent)?'Windows':/Macintosh/.test(agent)?'macOS':/Linux/.test(agent)?'Linux':'其他系统';return browser+' · '+system+'（仅供参考）';};
  const local=value=>{const date=new Date(value*1000);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);};
  const field=(name,label,value='',type='text',extra='')=>`<label class="field"><span>${e(label)}</span><input name="${name}" type="${type}" value="${e(value)}" ${extra}></label>`;
  const area=(name,label,value='')=>`<label class="field full"><span>${e(label)}</span><textarea name="${name}" rows="3">${e(value)}</textarea></label>`;
  const select=(name,label,items,value='',extra='')=>`<label class="field"><span>${e(label)}</span><select class="select" name="${name}" ${extra}>${items.map(x=>{const [key,label]=Array.isArray(x)?x:[x,x];return `<option value="${e(key)}" ${key===value?'selected':''}>${e(label)}</option>`;}).join('')}</select></label>`;
  const checks=(name,label,items,selected=[])=>`<div class="field full"><label>${e(label)}</label><div class="team-checks">${items.map(([id,label])=>`<label><input type="checkbox" name="${name}" value="${e(id)}" ${selected.includes(id)?'checked':''}>${e(label)}</label>`).join('')||'<span class="team-note">暂无可选对象</span>'}</div></div>`;
  const btn=(action,label,id='',extra='')=>`<button type="button" class="btn" data-team="${action}" data-id="${e(id)}" ${extra}>${e(label)}</button>`;
  function close(){$('#team-modal')?.remove();}
  function modal(title,body,save=null,label='保存'){
    close();const root=document.createElement('div');root.className='modal-backdrop open';root.id='team-modal';
    root.innerHTML=`<form class="modal team-dialog" id="team-form"><div class="modal-head"><h2>${e(title)}</h2>${btn('close','×')}</div><div class="modal-body"><div class="form-grid">${body}</div><div class="team-error" role="alert"></div></div><div class="modal-foot">${btn('close','关闭')}${save?`<button type="submit" class="btn primary">${e(label)}</button>`:''}</div></form>`;
    document.body.append(root);root.onclick=event=>{if(event.target===root)close();};
    if(save)root.querySelector('form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;const output=form.querySelector('.team-error');output.textContent='';const button=form.querySelector('[type=submit]');button.disabled=true;try{if(await save(form)!==false)close();}catch(error){output.textContent=error.message;}finally{button.disabled=false;}};
    root.querySelector('input,select,textarea')?.focus();return root.querySelector('form');
  }
  async function secureRequest(path,data){
    try{return await req(path,data);}catch(error){if(error.status!==428)throw error;}
    return new Promise((resolve,reject)=>{
      const form=modal('验证本人密码',field('password','当前密码','','password','autocomplete="current-password" required'),async f=>{await req('me/reauth',{password:f.password.value});resolve(await req(path,data));return false;},'验证并继续');
      form.querySelectorAll('[data-team=close]').forEach(b=>b.addEventListener('click',()=>reject(new Error('已取消操作。')),{once:true}));
    });
  }
  async function refresh(){
    if(!me()||me().must_change)return;
    const result=await Promise.all([req('tasks'),req('reviews'),req('members'),me().role==='admin'?req('admin/users'):Promise.resolve({users:[]})]);
    tasks=result[0].tasks;reviews=result[1].reviews;members=result[2].members;users=result[3].users;
    renderTasks();renderUsers();decorate();
  }
  function clear(){tasks=[];reviews=[];members=[];users=[];close();$('#team-tasks')?.replaceChildren();$('#team-users')?.replaceChildren();}
  function canSchedule(cid){return manage()||me()?.role==='head'&&tasks.some(t=>t.effective&&t.data.members.includes(me().username)&&t.data.allow_schedule&&t.data.candidates.includes(cid));}
  function decorate(){
    if(!me()||me().must_change)return;
    const p=api().permissions;
    $('.nav [data-view=users]').hidden=me().role!=='admin';
    $('.nav [data-view=ranking]').hidden=!manage();
    for(const selector of ['#top-import','#add-applicant','#import-csv','#export-backup','#restore-backup'])$(selector).hidden=me().role!=='admin';
    for(const selector of ['#top-schedule','#auto-schedule','#add-schedule'])$(selector).hidden=!p.schedule||(selector==='#auto-schedule'&&me().role==='head');
    $('#dept-availability-panel').hidden=!p.settings;
    $('#new-record').hidden=!manage();
    for(const el of $$('#applicant-body [onclick^="prefillSchedule"]')){const cid=el.getAttribute('onclick').match(/'([^']+)'/)?.[1];el.hidden=!canSchedule(cid);}
    for(const el of $$('#view-schedule [onclick^="editSchedule"]'))el.hidden=!p.schedule;
    for(const el of $$('#view-ranking [onclick^="moveRank"],#view-ranking [onclick^="swapRank"]'))el.hidden=!manage();
    if(!manage())for(const el of $$('#btn-batch-schedule-sms,#btn-batch-result-sms,#btn-export-sms-csv'))el.hidden=true;
    if(!$('#team-shortcuts')){
      const shortcuts=document.createElement('div');shortcuts.id='team-shortcuts';shortcuts.className='team-actions';
      shortcuts.innerHTML=btn('task-new','＋ 通用待办','','data-kind="general"')+btn('task-new','分配面试','','data-kind="interview"')+btn('task-new','分配通知','','data-kind="notice"');
      $('#view-schedule .section-head').after(shortcuts);
    }
    for(const b of $$('#team-shortcuts [data-team=task-new]'))b.hidden=['head','staff'].includes(me().role)&&b.dataset.kind!=='general';
    if(!$('#team-home-count')){const node=document.createElement('div');node.className='notice';node.id='team-home-count';$('#view-dashboard').prepend(node);}
    const count=tasks.filter(t=>t.effective&&t.data.members.includes(me().username)&&!t.data.private).length;
    $('#team-home-count').innerHTML=`<div>我的待办任务 <b>${count}</b> 项 · 面试意见与部门最终记录分别保存</div>${btn('go-tasks','查看任务')}`;
  }
  function onNavigate(view){if(view==='users'&&me()?.role!=='admin')return app().navigate('dashboard');if(view==='tasks'||view==='users')refresh().catch(error=>app().toast(error.message));}
  function table(head,rows){return `<article class="card"><div class="table-wrap"><table class="team-table"><thead><tr>${head.map(h=>`<th>${e(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${head.length}">暂无符合条件的记录</td></tr>`}</tbody></table></div></article>`;}
  function renderTasks(){
    if(!$('#team-tasks')||!me())return;
    const subset=tasks.filter(t=>(filter.scope!=='mine'||t.data.members.includes(me().username))&&(filter.scope!=='created'||t.created_by===me().username)&&(!filter.kind||t.kind===filter.kind)&&(!filter.status||t.data.status===filter.status)&&(!filter.department||t.department===filter.department)&&(!filter.search||(t.data.title+' '+person(t.data.assignee)).includes(filter.search)));
    $('#team-tasks').innerHTML=`<div class="team-toolbar">${select('scope','范围',[['mine','我的任务'],['created','我分配的'],['department',me().role==='admin'?'全部任务':'部门可见任务']],filter.scope)}${select('kind','类型',[['','全部类型'],...Object.entries(kinds)],filter.kind)}${select('status','状态',[['','全部状态'],...states],filter.status)}${select('department','部门',[['','全部可见部门'],...departments],filter.department)}${field('search','搜索',filter.search)}${btn('task-new','＋ 新任务')}</div>`+
      table(['任务','分工与期限','状态与进度','操作'],subset.map(t=>`<tr><td><strong>${e(t.data.title)}</strong><small>${e(kinds[t.kind])} · ${e(t.department)}${t.data.private?' · 私人':''}</small></td><td>${e(person(t.data.assignee))}<small>${e(when(t.data.due))}</small></td><td>${e(t.data.status)}${t.overdue?' · 已逾期':''}<small>${!t.effective?'已关闭、安排变化或授权失效':e(progress(t))}</small></td><td>${btn('task-open','查看',t.id)}${t.can_manage&&!['已完成','已取消'].includes(t.data.status)?btn('task-edit','编辑或改派',t.id):''}${t.can_delegate?btn('task-delegate','分配给工作人员',t.id):''}</td></tr>`));
    for(const input of $$('#team-tasks .team-toolbar input,#team-tasks .team-toolbar select'))input.onchange=()=>{filter[input.name]=input.value;renderTasks();};
  }
  function progress(t){
    if(t.kind==='notice')return `${Object.values(t.data.progress).filter(x=>x.status==='已联系').length}/${t.data.candidates.length} 已联系`;
    if(t.kind==='interview'){const needed=t.data.candidates.length*t.data.reviewers.length;const submitted=reviews.filter(r=>r.department===t.department&&t.data.candidates.includes(r.candidate)&&t.data.reviewers.includes(r.author)&&r.status==='submitted').length;return `${submitted}/${needed} 可见已提交个人意见（只显示本人或可复核意见）`;}
    return t.data.priority;
  }
  async function taskForm(id='',preset={}){
    const old=tasks.find(t=>t.id===id),d={...old?.data,...preset};
    const kind=old?.kind||preset.kind||'general',dept=old?.department||preset.department||currentDept();
    const parent=tasks.find(t=>t.id===d.parent);
    const state=app().getState();
    let available=members.filter(u=>u.department===dept||me().role==='admin'&&u.role==='admin');
    if(me().role==='lead')available=available.filter(u=>['head','staff'].includes(u.role));
    if(me().role==='head')available=available.filter(u=>u.role==='staff');
    if(me().role==='staff'||d.private)available=members.filter(u=>u.username===me().username);
    const owner=d.assignee||available[0]?.username||me().username,selected=d.members||[owner];
    const candidates=state.candidates.filter(c=>c.prefs.includes(dept)&&(!parent||parent.data.candidates.includes(c.id)));
    const schedules=state.schedules.filter(s=>s.dept===dept&&(!parent||parent.data.schedule_ids.includes(String(s.id))));
    const expiry=d.access_until||Math.min(Math.floor(Date.now()/1000)+7*86400,parent?.data.access_until||Infinity);
    const form=modal(old?'编辑或改派任务':parent?'分配子任务':'分配新任务',
      field('title','任务名称',d.title||'','text','required maxlength="120"')+select('kind','类型',!parent&&['head','staff'].includes(me().role)?[['general','通用待办']]:Object.entries(kinds),kind,old?'disabled':'')+
      select('department','部门',me().role==='admin'?departments:[me().department],dept,old||parent?'disabled':'')+
      select('priority','优先级',['普通','优先','紧急'],d.priority||'普通')+
      area('description','任务说明',d.description||'')+
      select('assignee','主执行人',available.map(u=>[u.username,`${u.display_name} · ${roles[u.role]}`]),owner)+
      checks('members','协作人员（主执行人自动包含）',available.map(u=>[u.username,`${u.display_name} · ${roles[u.role]}`]),selected.filter(name=>name!==owner))+
      (kind==='interview'?checks('schedule_ids','关联面试安排',schedules.map(s=>[String(s.id),`${cName(s.candidate)} · ${s.date} ${s.time}—${s.endTime}`]),d.schedule_ids||[])+checks('reviewers','面试评价人员（自动计入成员）',available.filter(u=>u.can_interview).map(u=>[u.username,u.display_name]),d.reviewers||available.filter(u=>selected.includes(u.username)&&u.can_interview).map(u=>u.username))+
        select('allow_schedule','授权负责人调整这些学生排期',[['0','不授予'],['1','授予']],d.allow_schedule?'1':'0')+
        select('allow_settings','授权负责人维护部门面试时间设置',[['0','不授予'],['1','授予']],d.allow_settings?'1':'0'):'')+
      (kind==='notice'?checks('candidates','通知学生',candidates.map(c=>[c.id,`${c.name} · ${c.id}`]),d.candidates||[])+area('template','通知模板',d.template||state.notificationTemplates.interview||'{姓名}同学，请参加{部门}面试，时间为{面试日期} {面试时间}，地点{面试地点}。'):'')+
      (kind==='general'?area('checklist','检查项或完成要求',d.checklist||'')+select('private','可见范围',me().role==='staff'?[['1','本人私人待办']]:[['0','部门任务'],['1','本人私人待办']],d.private||me().role==='staff'?'1':'0'):'')+
      field('due','截止时间（逾期可继续跟进）',d.due?local(d.due):'','datetime-local')+field('access_until','授权有效期至',local(expiry),'datetime-local','required')+
      `<p class="team-note field full">${parent?'上级任务：'+e(parent.data.title)+'。范围与期限不能扩大。':'仅分配本部门有权处理的工作。'}通知由人工发送；通用待办不携带学生资料。新改派保留历史意见与联系记录。</p>`,
      async f=>{
        const pick=name=>[...f.querySelectorAll(`input[name="${name}"]:checked`)].map(x=>x.value);
        const ids=pick('schedule_ids');const people=[...new Set([...pick('members'),...pick('reviewers'),f.assignee.value])];
        await req('tasks/save',{id:old?.id,version:old?.version,kind,department:dept,title:f.title.value,description:f.description.value,
          members:people,assignee:f.assignee.value,reviewers:pick('reviewers'),candidates:kind==='interview'?[...new Set(schedules.filter(s=>ids.includes(String(s.id))).map(s=>s.candidate))]:pick('candidates'),schedule_ids:ids,
          fields:kind==='notice'?['contact']:[],template:f.template?.value||'',checklist:f.checklist?.value||'',priority:f.priority.value,
          parent:d.parent||'',allow_schedule:f.allow_schedule?.value==='1',allow_settings:f.allow_settings?.value==='1',private:f.private?.value==='1',
          due:f.due.value?Math.floor(new Date(f.due.value).getTime()/1000):0,access_until:Math.floor(new Date(f.access_until.value).getTime()/1000)});
        close();await api().load();await refresh();return false;
      },'保存分工');
    let previousOwner=owner;
    form.assignee.onchange=()=>{
      if(!old){for(const input of form.querySelectorAll('input[name=reviewers]')){
        if(input.value===previousOwner&&!form.querySelector(`input[name=members][value="${previousOwner}"]:checked`))input.checked=false;
        if(input.value===form.assignee.value)input.checked=true;
      }}
      previousOwner=form.assignee.value;
    };
    const preserve=()=>({title:form.title.value,description:form.description.value,kind:form.kind.value,department:form.department.value,parent:d.parent||'',private:form.private?.value==='1'});
    if(!old&&!parent){form.kind.onchange=()=>taskForm('',preserve());form.department.onchange=()=>taskForm('',preserve());}
    if(kind==='general')form.private.onchange=()=>taskForm(old?.id||'',{...d,...preserve(),private:form.private.value==='1'});
  }
  async function taskOpen(id){
    await refresh();
    const t=tasks.find(t=>t.id===id);if(!t)return;
    const d=t.data;
    let body=`<div class="field full"><h3>${e(kinds[t.kind])} · ${e(t.department)}</h3><p class="team-pre">${e(d.description)}</p><p class="team-note">主执行人 ${e(person(d.assignee))} · ${e(d.status)} · ${e(progress(t))}<br>截止 ${e(when(d.due))}；授权至 ${e(when(d.access_until))}</p>${!t.effective?'<p class="team-error">任务已关闭、授权失效或原安排变化。敏感执行内容已清理，请联系分配人。</p>':''}<p class="team-pre">${e(d.checklist)}</p></div>`;
    if(t.effective&&t.kind==='interview')for(const cid of d.candidates){
      const own=reviews.find(r=>r.candidate===cid&&r.department===t.department&&r.author===me().username);
      body+=`<div class="field full"><strong>${e(cName(cid))} · ${e(cid)}</strong><div class="team-actions">${d.reviewers.includes(me().username)?btn('review','我的面试意见',id,`data-candidate="${e(cid)}"`):''}${manage()?btn('department-record','填写部门最终记录',id,`data-candidate="${e(cid)}" data-dept="${e(t.department)}"`):''}</div>${own?`<p>我的意见：${own.status==='draft'?'草稿':'已提交'}</p>`:''}${reviews.filter(r=>r.candidate===cid&&r.department===t.department&&r.status==='submitted').map(r=>`<div class="team-event">${e(person(r.author))}：${e(r.record.decision)} ${e(r.record.score)}<p class="team-pre">${e(r.record.note)}</p>${manage()?btn('review-return','退回本人修改',r.id):''}</div>`).join('')}</div>`;
    }
    if(t.effective&&t.kind==='notice')for(const cid of d.candidates){const pr=d.progress[cid]||{};body+=`<div class="field full"><strong>${e(cName(cid))} · ${e(pr.status||'待联系')}</strong><p>${e(pr.note||'')}</p><div class="team-actions">${btn('message','查看文本与必要联系方式',id,`data-candidate="${e(cid)}"`)}${t.can_execute||t.can_manage?btn('notice-progress','登记联系状态',id,`data-candidate="${e(cid)}"`):''}</div></div>`;}
    body+='<div class="field full team-actions">';
    if(t.can_execute&&d.status==='待开始')body+=btn('task-action','开始处理',id,'data-action="start"');
    if(t.can_execute&&['待开始','进行中'].includes(d.status))body+=btn('task-action','记录进度',id,'data-action="progress"')+btn('task-action',d.private?'完成':'提交完成审核',id,'data-action="submit"');
    if(t.can_manage&&d.status==='待审核')body+=btn('task-action','审核通过',id,'data-action="approve"')+btn('task-action','退回补充',id,'data-action="return"');
    if(t.can_manage&&!['已完成','已取消'].includes(d.status))body+=btn('task-action','取消任务',id,'data-action="cancel"');
    body+='</div><div class="field full"><h3>操作记录</h3>'+t.events.map(v=>`<div class="team-event">${e(when(v.at))} · ${e(v.actor)} · ${e(v.action)}<p>${e(v.note)}</p></div>`).join('')+'</div>';
    modal(d.title,body);
  }
  async function taskAction(id,action,candidate=''){
    const t=tasks.find(t=>t.id===id);if(!t)return;
    const form=modal('更新任务',action==='progress'&&t.kind==='notice'?'<p class="field full">请在各学生的“登记联系状态”中操作。</p>':area('note',['return','cancel'].includes(action)?'原因（必填）':'完成说明或进度'),action==='progress'&&t.kind==='notice'?null:async f=>{
      await req('tasks/action',{id,version:t.version,action,note:f.note.value});close();await api().load();await refresh();taskOpen(id);return false;
    },'确认');
  }
  async function noticeProgress(id,cid){
    const t=tasks.find(t=>t.id===id),pr=t.data.progress[cid]||{};
    modal('登记联系状态',select('status','状态',['待联系','已联系','需跟进'],pr.status||'待联系')+area('note','跟进说明',pr.note||''),async f=>{
      await req('tasks/action',{id,version:t.version,action:'progress',candidate:cid,status:f.status.value,note:f.note.value});close();await refresh();taskOpen(id);return false;
    });
  }
  async function personalReview(cid,dept,taskId=''){
    await refresh();
    const t=tasks.find(t=>t.effective&&t.kind==='interview'&&t.department===dept&&t.data.candidates.includes(cid)&&t.data.reviewers.includes(me().username)&&(!taskId||t.id===taskId));
    if(!t)throw new Error('尚未分配有效面试任务或没有面试资格，请联系负责人。');
    const r=reviews.find(r=>r.candidate===cid&&r.department===dept&&r.author===me().username),record=r?.record||{};
    if(r?.status==='submitted'){modal('已提交个人意见',`<div class="field full"><p>${e(record.decision)} ${e(record.score)}</p><p class="team-pre">${e(record.note)}</p><p class="team-note">需要修订时请主要负责人退回本人。其他面试人的草稿不可见。</p></div>`);return;}
    const form=modal(`我的面试意见 · ${cName(cid)}`,['沟通表达','责任与投入','部门匹配'].map((label,i)=>select('dim'+i,label,['突出','稳定','待观察'],record.dims?.[i]||'稳定')).join('')+select('decision','综合意见',['推荐','候补观察','不建议'],record.decision||'推荐')+field('score','综合分（0—100，可留空）',record.score||'','number','min="0" max="100"')+area('note','表现与判断依据',record.note||'')+select('action','保存方式',[['draft','暂存本人草稿'],['submit','正式提交意见']],'draft')+'<p class="field full team-note">此处只保存本人意见，不会覆盖部门最终记录。提交后由统筹人员复核。</p>',async f=>{
      await req('reviews/action',{action:f.action.value,task:t.id,candidate:cid,department:dept,version:r?.version,record:{dims:[f.dim0.value,f.dim1.value,f.dim2.value],decision:f.decision.value,score:f.score.value,note:f.note.value}});await refresh();return true;
    });
  }
  async function message(id,cid){
    const [textResult,contact]=await Promise.all([req('notification-text',{task:id,candidate:cid}),req('contact',{task:id,candidate:cid})]);
    modal('人工通知',area('message','通知文本',textResult.text)+`<div class="field full"><label>必要联系方式</label><p class="team-secret">${e(contact.contact||'原报名表未填写')}</p>${btn('copy','复制通知文本')}<p class="team-note">请在原渠道人工发送，再登记联系状态；复制不会自动标记已联系。</p></div>`);
  }
  function renderUsers(){
    if(!$('#team-users')||me()?.role!=='admin')return;
    const list=users.filter(u=>(!userFilter.role||u.role===userFilter.role)&&(!userFilter.department||u.department===userFilter.department)&&(!userFilter.search||(u.username+' '+u.display_name).includes(userFilter.search)));
    $('#team-users').innerHTML=`<div class="team-toolbar">${select('role','岗位',[['','全部岗位'],...Object.entries(roles)],userFilter.role)}${select('department','部门',[['','全部部门'],...departments],userFilter.department)}${field('search','搜索',userFilter.search)}${btn('user-create','＋ 创建账号')}</div>`+table(['个人账号','岗位与部门','状态与登录','操作'],list.map(u=>`<tr><td><strong>${e(u.display_name)}</strong><small>${e(u.username)}</small></td><td>${e(roles[u.role])}<small>${e(u.department||'全局')}${u.can_interview?' · 面试资格':''}</small></td><td>${u.active?(u.must_change?'等待首次改密':'已启用'):'已停用'}<small>${u.session?'有效登录 '+e(when(u.session.created)):'暂无有效登录'}</small><small>${e(browserLabel(u.session?.agent))}</small></td><td>${btn('user-edit','岗位与状态',u.username)}${u.username!==me().username?btn('user-reset','重置密码',u.username):''}${btn('user-revoke','撤销登录',u.username)}</td></tr>`));
    for(const input of $$('#team-users .team-toolbar input,#team-users .team-toolbar select'))input.onchange=()=>{userFilter[input.name]=input.value;renderUsers();};
  }
  function userForm(name=''){
    const u=users.find(u=>u.username===name);
    const form=modal(u?'修改账号':'创建个人账号',(!u?field('username','账号（3—40位字母数字下划线连字符）','','text','required'):'')+field('display_name','姓名或内部称呼',u?.display_name||'','text','required')+
      select('role','岗位',Object.entries(roles),u?.role||'staff')+select('department','所属部门',[['','全局（仅总负责人）'],...departments],u?.department||currentDept())+
      select('can_interview','面试资格',[['0','未授予'],['1','已授予']],u?.can_interview?'1':'0')+(u?select('active','账号状态',[['1','启用'],['0','停用']],u.active?'1':'0'):'')+'<p class="field full team-note">新账号临时密码只显示一次，24小时有效，首次登录设置正式密码。修改岗位或状态会撤销原登录。</p>',async f=>{
        const data={username:u?.username||f.username.value,revision:u?.revision,display_name:f.display_name.value,role:f.role.value,department:f.department.value,can_interview:f.can_interview.value==='1',active:!u||f.active.value==='1'};
        const result=await secureRequest('admin/users/'+(u?'edit':'create'),data);close();
        if(result.temporary_password){await refresh();modal('请私下交付临时密码',`<div class="field full"><p>账号 ${e(data.username)}</p><p class="team-secret">${e(result.temporary_password)}</p><p class="team-note">此密码只显示一次，请勿发到公开群或项目仓库。24小时内首次登录并改密。</p></div>`);}else{await refresh();await api().load();}
        return false;
      });
    form.role.onchange=()=>{if(form.role.value==='admin')form.department.value='';else if(!form.department.value)form.department.value=currentDept();};
  }
  async function userAction(name,action){
    const u=users.find(u=>u.username===name);
    if(!confirm(action==='reset'?'重置后旧登录立即失效，是否继续？':'撤销该账号当前登录？'))return;
    const result=await secureRequest('admin/users/'+action,{username:name,revision:u.revision});
    if(result.temporary_password)modal('新的临时密码',`<div class="field full"><p>${e(name)}</p><p class="team-secret">${e(result.temporary_password)}</p><p>24小时有效，首次登录必须改密。</p></div>`);
    await refresh();
  }
  function passwordForm(){modal('修改本人密码',field('old','当前密码','','password','autocomplete="current-password" required')+field('next','新密码（8—128字符）','','password','autocomplete="new-password" required')+field('confirm','再次输入新密码','','password','autocomplete="new-password" required'),async f=>{if(f.next.value!==f.confirm.value)throw new Error('两次密码不一致。');await req('me/password',{old_password:f.old.value,password:f.next.value});api().expire('密码已修改，请重新登录。');});}
  function detail(cid){
    if(!manage())return;
    const section=document.createElement('div');section.className='detail-section';section.innerHTML='<h4>必要联系方式</h4>'+btn('reveal','按需查看联系方式',cid)+'<div id="team-contact"></div>';
    $('#detail-body').prepend(section);
  }
  const actions={
    close:()=>close(),'go-tasks':()=>app().navigate('tasks'),
    'task-new':el=>taskForm('',{kind:el.dataset.kind||'general',private:me().role==='staff'}),
    'task-edit':el=>taskForm(el.dataset.id),
    'task-delegate':el=>{const t=tasks.find(t=>t.id===el.dataset.id);return taskForm('',{kind:t.kind,department:t.department,parent:t.id,access_until:t.data.access_until,template:t.data.template,allow_schedule:t.data.allow_schedule});},
    'task-open':el=>taskOpen(el.dataset.id),'task-action':el=>taskAction(el.dataset.id,el.dataset.action),
    'notice-progress':el=>noticeProgress(el.dataset.id,el.dataset.candidate),message:el=>message(el.dataset.id,el.dataset.candidate),
    review:el=>{const t=tasks.find(t=>t.id===el.dataset.id);return personalReview(el.dataset.candidate,t.department,t.id);},
    'department-record':el=>{close();app().viewDepartment(el.dataset.dept);window.openRecordFor(el.dataset.candidate,el.dataset.dept);},
    'review-return':el=>{const r=reviews.find(r=>r.id===el.dataset.id);modal('退回个人意见',area('reason','修订原因'),async f=>{await req('reviews/action',{action:'return',candidate:r.candidate,department:r.department,author:r.author,version:r.version,reason:f.reason.value});await refresh();});},
    copy:async()=>{const input=$('#team-form textarea[name=message]');await navigator.clipboard.writeText(input.value);app().toast('已复制文本，请人工发送');},
    'user-create':()=>userForm(),'user-edit':el=>userForm(el.dataset.id),'user-reset':el=>userAction(el.dataset.id,'reset'),'user-revoke':el=>userAction(el.dataset.id,'revoke'),
    reveal:async el=>{const result=await req('contact',{candidate:el.dataset.id});const node=$('#team-contact');if(node){node.textContent=result.contact||'原报名表未填写';setTimeout(()=>node.textContent='',60000);}}
  };
  document.addEventListener('click',event=>{const el=event.target.closest('[data-team]');if(el){event.preventDefault();Promise.resolve().then(()=>actions[el.dataset.team]?.(el)).catch(error=>{const target=$('#team-modal .team-error');if(target)target.textContent=error.message;else app()?.toast(error.message);});}});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
  window.RecruitTeam={refresh,clear,decorate,onNavigate,personalReview:(cid,dept)=>personalReview(cid,dept).catch(error=>app().toast(error.message)),passwordForm,detail,canSchedule,secureRequest};
})();
