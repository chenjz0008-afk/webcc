let key = '', refreshTimer;
const $ = id => document.getElementById(id);
const labels = {ready:'就绪',paused:'已暂停',quarantined:'已隔离',updating:'更新中',pending:'创建中',error:'创建异常'};
function notice(message) { $('notice').textContent = message; $('notice').hidden = false; setTimeout(() => $('notice').hidden = true, 6500); }
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers:{Authorization:`Bearer ${key}`, 'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || `HTTP ${response.status}`);
  return data;
}
function element(tag, text, className) { const node=document.createElement(tag); if(text !== undefined) node.textContent=text; if(className) node.className=className; return node; }
function detail(list, title, value) { list.append(element('dt',title),element('dd',value)); }
async function refresh() {
  const data = await api('/admin/accounts');
  $('count').textContent=data.accounts.length; $('ready').textContent=data.accounts.filter(a=>a.status==='ready'&&a.running).length;
  $('inflight').textContent=`${data.inflight} / ${data.waiting}`; $('api-base').textContent=location.origin+'/v1';
  $('accounts').replaceChildren();
  for(const account of data.accounts) {
    const card=element('article',undefined,'account'), head=element('div',undefined,'account-head');
    head.append(element('h3',account.name),element('span',labels[account.status] || account.status,'tag'+(account.status==='quarantined'||account.status==='error'?' bad':''))); card.append(head);
    const list=element('dl'); detail(list,'容器',account.container); detail(list,'进程状态',account.running?'running':account.docker_status);
    detail(list,'独立代理',account.proxy); detail(list,'本机端口',`127.0.0.1:${account.port} → 8484`); detail(list,'数据挂载',account.mounts.map(m=>`${m.source} → ${m.destination}`).join('\n') || '未检测到挂载');
    detail(list,'生成验证',account.verified_at?new Date(account.verified_at*1000).toLocaleString():'尚未验证'); detail(list,'进行中',account.inflight); detail(list,'版本',account.version||'未知'); card.append(list);
    if(account.reason)card.append(element('p',account.reason,'warn'));
    const details=element('details'); details.append(element('summary','镜像与浏览器资料'),element('p',account.image),element('p',account.user_agent||'没有 UA 资料'),element('p','系统标签：'+(account.os_label||'未提供')),element('p',account.fingerprint));card.append(details);
    const actions=element('div',undefined,'actions');
    for(const [action,title] of [['probe','生成验证'],[account.status==='ready'?'pause':'resume',account.status==='ready'?'暂停':'人工恢复']]) {
      const button=element('button',title,'secondary'); button.disabled=account.status==='updating'||account.status==='pending'||(action==='probe'&&account.status!=='ready');
      button.onclick=async()=>{ if(action==='resume'&&account.status==='quarantined'&&!confirm('该账号因异常被隔离。确认已核查原因并人工恢复？'))return; button.disabled=true; try{const result=await api(`/admin/accounts/${account.id}/${action}`,'POST');notice(result.content?`验证成功：${result.content}`:'操作完成');}catch(e){notice(e.message);}finally{await refresh();} };actions.append(button);
    }card.append(actions);$('accounts').append(card);
  }
  if(!data.accounts.length)$('accounts').append(element('div','还没有账号。填写代理和 sessionKey，或导入账号 txt。','empty'));
  const update=data.update; $('update-status').textContent=`${update.message||'尚未检测'}${update.latest_sha?' · 最新 '+update.latest_sha.slice(0,12):''}${update.deployed_sha?' · 已部署 '+update.deployed_sha.slice(0,12):''}${update.checked_at?' · 检查于 '+new Date(update.checked_at*1000).toLocaleString():''}`;
}
$('login-form').onsubmit=async event=>{event.preventDefault();key=$('admin-key').value;try{await refresh();$('admin-key').value='';$('login').hidden=true;$('dashboard').hidden=false;refreshTimer=setInterval(()=>refresh().catch(()=>{}),10000);}catch(e){key='';notice(e.message);}};
$('logout').onclick=()=>{key='';clearInterval(refreshTimer);$('dashboard').hidden=true;$('login').hidden=false;$('accounts').replaceChildren();};
$('refresh').onclick=()=>refresh().catch(e=>notice(e.message));
$('account-file').onchange=async event=>{const file=event.target.files[0];if(!file)return;if(file.size>1024*1024)return notice('文件不能超过 1 MiB');const text=await file.text(),lines=text.split(/\r?\n/).map(v=>v.trim()).filter(Boolean),header=lines.slice(0,lines.indexOf('[')>=0?lines.indexOf('['):20),form=$('account-form');form.elements.name.value=file.name.replace(/\.txt$/i,'');form.elements.sessionKey.value=lines[0]||'';form.elements.proxy.value=header.find(v=>/^(?:\w+:\/\/|\d+\.\d+\.\d+\.\d+:\d+)/.test(v))||'';form.elements.user_agent.value=header.find(v=>v.startsWith('Mozilla/'))||'';form.elements.os_label.value=header.find(v=>/^(?:macOS|Windows|Linux|Android)\S*/i.test(v))||'';form.elements.proxy_username.value='';form.elements.proxy_password.value='';notice('已提取文件头；其他站点 Cookie 不会导入。');};
$('account-form').onsubmit=async event=>{event.preventDefault();$('create').disabled=true;try{await api('/admin/accounts','POST',Object.fromEntries(new FormData(event.target)));event.target.reset();$('account-file').value='';notice('容器已挂载，请执行生成验证。');await refresh();}catch(e){notice(e.message);}finally{$('create').disabled=false;}};
$('update').onclick=async()=>{$('update').disabled=true;try{await api('/admin/update','POST');notice('更新检查已启动，页面会自动刷新状态。');}catch(e){notice(e.message);}finally{$('update').disabled=false;}};
