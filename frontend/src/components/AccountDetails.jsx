import React, { useEffect, useState } from 'react';
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, LinearProgress, Stack, Tab, Tabs, Typography } from '@mui/material';
import { copyText, expiration, formatDate, statuses, toLocalDate, toTimestamp } from '../accounts.js';
import ProfileFields from './ProfileFields.jsx';
import SecretField from './SecretField.jsx';
import CopyField from './CopyField.jsx';

function DetailsList({ items, notify }) {
  return <Box component="dl" sx={{ m: 0, display: 'grid', gridTemplateColumns: '82px minmax(0,1fr) 42px', gap: '16px 12px', alignItems: 'start' }}>
    {items.map(([label, value]) => <React.Fragment key={label}><Typography component="dt" variant="body2" color="text.secondary">{label}</Typography><Typography component="dd" variant="body2" sx={{ m: 0, overflowWrap: 'anywhere', whiteSpace: ['专用代理', '出口 IP'].includes(label) ? 'nowrap' : 'pre-line', overflowX: 'auto' }}>{value || '未填写'}</Typography><Button size="small" aria-label={`复制${label}`} disabled={!value} sx={{ minWidth: 0, p: 0 }} onClick={() => copyText(value, notify)}>复制</Button></React.Fragment>)}
  </Box>;
}

export default function AccountDetails({ account, api, refresh, notify, now }) {
  const [tab, setTab] = useState(0), [fields, setFields] = useState(null), [baseline, setBaseline] = useState(null);
  const [currentSession, setCurrentSession] = useState(''), [busy, setBusy] = useState(false), [loadError, setLoadError] = useState(''), [confirm, setConfirm] = useState(null);
  const [label, color] = statuses[account.status] || [account.status, 'default'];
  const expiry = expiration(account, now);
  const change = name => event => setFields(current => ({ ...current, [name]: event.target.value, ...(name === 'sessionKey' && !current.sessionKey && event.target.value ? { expires: '' } : {}) }));
  const basePath = `/admin/accounts/${account.id}`;
  function acceptDetails(value) {
    const next = { ...value, sessionKey: '', expires: toLocalDate(value.session_expires_at) };
    setCurrentSession(value.sessionKey); setFields(next); setBaseline(next);
  }
  useEffect(() => {
    let cancelled = false;
    api(`${basePath}/details`).then(value => { if (!cancelled) acceptDetails(value); }).catch(error => { if (!cancelled) setLoadError(error.message); });
    return () => { cancelled = true; };
  }, [api, basePath]);
  const dirty = fields && JSON.stringify(fields) !== JSON.stringify(baseline);
  let proxyString = '';
  if (fields) {
    try { const proxy = new URL(fields.proxy); proxyString = `${proxy.hostname}:${proxy.port}:${fields.proxy_username}:${fields.proxy_password}`; }
    catch { proxyString = ''; }
  }
  const unavailable = busy || ['pending', 'updating'].includes(account.status);
  async function act(action) {
    setConfirm(null); setBusy(true);
    try {
      const result = await api(`${basePath}/${action}`, 'POST');
      notify(result.content ? `验证成功：${result.content}` : result.ip ? `代理可用，出口 IP ${result.ip}` : '操作完成');
    } catch (error) { notify(error.message, 'error'); }
    finally { setBusy(false); await refresh(); }
  }
  async function save() {
    setConfirm(null); setBusy(true);
    try {
      const { expires, session_expires_at, ...values } = fields;
      const result = await api(`${basePath}/details`, 'POST', { ...values, session_expires_at: expires === baseline.expires ? baseline.session_expires_at : toTimestamp(expires) });
      acceptDetails(await api(`${basePath}/details`));
      await refresh();
      notify(result.restarted ? '连接配置已更新，请执行生成验证。' : '资料已保存');
    } catch (error) { notify(error.message, 'error'); }
    finally { setBusy(false); }
  }
  function submit(event) {
    event.preventDefault();
    const connectionChanged = fields.sessionKey.trim() || ['proxy', 'proxy_username', 'proxy_password'].some(key => fields[key] !== baseline[key]);
    if (connectionChanged) setConfirm('save'); else save();
  }
  const confirmation = {
    save: ['更新连接配置？', '等待当前请求结束后重新加载容器。暂停、停用或隔离的账号保持原状态；更新后请进行生成验证。'],
    resume: ['恢复账号？', '确认异常原因已处理。恢复后，账号将重新接收请求。'],
    disable: ['标记为不可用？', '账号将停止接收新请求，等待当前请求结束后停止容器，并移到列表底部。资料会保留。'],
  }[confirm];
  return <Stack spacing={2.5}>
    <Stack direction="row" alignItems="center" justifyContent="space-between"><Box><Stack direction="row" alignItems="center"><Typography variant="h5" fontWeight={650}>{account.name}</Typography><Button size="small" aria-label="复制账号名称" onClick={() => copyText(account.name, notify)}>复制</Button></Stack><Stack direction="row" alignItems="center"><Typography variant="body2" color="text.secondary">{account.email || '邮箱未填写'}</Typography>{account.email && <Button size="small" aria-label="复制邮箱" onClick={() => copyText(account.email, notify)}>复制</Button>}</Stack></Box><Chip label={label} color={color} variant="outlined" size="small" onClick={() => copyText(label, notify)} title="点击复制状态" /></Stack>
    <Box sx={{ p: 2, bgcolor: 'background.default', borderRadius: 2 }}><Stack direction="row" alignItems="center" justifyContent="space-between"><Typography variant="caption" color="text.secondary">{account.session_expiry_source === 'estimated' ? 'sessionKey 预计有效期' : 'sessionKey 有效期'}</Typography><Button size="small" aria-label="复制到期时间" disabled={!account.session_expires_at} onClick={() => copyText(formatDate(account.session_expires_at), notify)}>复制</Button></Stack><Typography sx={{ mt: .5, fontWeight: 600, color: expiry.color }}>{expiry.label}</Typography><Typography variant="caption" color="text.secondary">{account.session_expires_at ? formatDate(account.session_expires_at) : '在资料中补充 Cookie 到期时间'}</Typography></Box>
    {account.reason && account.status !== 'ready' && <Alert severity="info" action={<Button size="small" aria-label="复制状态原因" onClick={() => copyText(account.reason, notify)}>复制</Button>}>{account.reason}</Alert>}
    <Tabs value={tab} onChange={(_, value) => setTab(value)} variant="fullWidth" sx={{ borderBottom: 1, borderColor: 'divider' }}><Tab label="概览" /><Tab label="资料与凭证" /><Tab label="容器" /><Tab label="浏览器" /></Tabs>
    {tab === 0 && <Stack spacing={3}>
      <DetailsList notify={notify} items={[
        ['专用代理', account.proxy], ['出口 IP', account.proxy_check?.ok ? account.proxy_check.ip : '未检测或检测失败'],
        ['检测耗时', account.proxy_check?.ok ? `${account.proxy_check.latency_ms} ms` : account.proxy_check?.message],
        ['生成验证', account.verified_at ? formatDate(account.verified_at) : '尚未验证'],
        ['连续通道失败', `${account.consecutive_failures || 0} 次`], ['最近通道错误', account.last_error ? `${account.last_error} · ${formatDate(account.last_error_at)}` : '无'],
        ['限流冷却截止', account.cooldown_until > Date.now() / 1000 ? formatDate(account.cooldown_until) : '未冷却'],
        ['请求负载', `${account.inflight} / 1`], ['已分配', `${account.dispatches || 0} 次`], ['备注', account.notes],
      ]} />
      <Divider />
      <Stack direction="row" gap={1} flexWrap="wrap">
        <Button variant="outlined" disabled={unavailable} onClick={() => act('proxy-test')}>检测代理</Button>
        <Button variant="contained" disabled={unavailable || account.status !== 'ready'} onClick={() => act('probe')}>生成验证</Button>
        {account.status === 'ready' ? <Button disabled={unavailable} onClick={() => act('pause')}>暂停</Button> : account.status !== 'expired' && <Button disabled={unavailable} onClick={() => setConfirm('resume')}>人工恢复</Button>}
      </Stack>
      <Button color="inherit" sx={{ alignSelf: 'flex-start', color: 'text.secondary' }} disabled={unavailable || account.status === 'disabled'} onClick={() => setConfirm('disable')}>标记不可用</Button>
    </Stack>}
    {tab === 1 && (loadError ? <Alert severity="error">{loadError}</Alert> : !fields ? <LinearProgress /> : <Stack component="form" spacing={3} onSubmit={submit}>
      <CopyField label="账号名称" required value={fields.name} onChange={change('name')} disabled={unavailable} notify={notify} slotProps={{ htmlInput: { maxLength: 80 } }} />
      <ProfileFields {...{ fields, change, notify }} disabled={unavailable} />
      <Divider /><Typography variant="subtitle2">代理配置</Typography>
      <SecretField label="代理连接串" value={proxyString} readOnly copy notify={notify} helperText="IP:端口:用户名:密码，可整串复制。" />
      <CopyField label="代理地址" required value={fields.proxy} onChange={change('proxy')} disabled={unavailable} notify={notify} />
      <CopyField label="代理用户名" value={fields.proxy_username} onChange={change('proxy_username')} disabled={unavailable} notify={notify} autoComplete="off" />
      <SecretField label="代理密码" value={fields.proxy_password} onChange={change('proxy_password')} disabled={unavailable} copy notify={notify} />
      <Divider /><Typography variant="subtitle2">sessionKey</Typography>
      <SecretField label="当前 sessionKey" value={currentSession} readOnly copy notify={notify} />
      <SecretField label="新的 sessionKey" value={fields.sessionKey} onChange={change('sessionKey')} disabled={unavailable} copy notify={notify} helperText="留空保留原值。更换时可填实际到期时间；不填则从更新时刻加 29 天估算。" />
      <Stack direction="row" spacing={1} sx={{ position: 'sticky', bottom: 0, py: 2, bgcolor: 'background.paper', borderTop: 1, borderColor: 'divider' }}><Button type="submit" variant="contained" disabled={unavailable || !dirty}>{busy ? '正在保存…' : '保存修改'}</Button><Button disabled={unavailable || !dirty} onClick={() => setFields(baseline)}>撤销修改</Button></Stack>
    </Stack>)}
    {tab === 2 && <DetailsList notify={notify} items={[
      ['容器', account.container], ['运行状态', account.running ? '运行中' : account.docker_status],
      ['本机端口', `127.0.0.1:${account.port} → 8484`], ['版本', account.version],
      ['配置挂载', account.mounts.map(m => `${m.source}\n→ ${m.destination}`).join('\n')],
      ['创建时间', formatDate(account.created_at)], ['凭证导入', formatDate(account.session_imported_at)], ['镜像', account.image],
    ]} />}
    {tab === 3 && <Stack spacing={3}>
      <DetailsList notify={notify} items={[
        ['系统标签', account.os_label],
        ['UA 系统', /Windows/i.test(account.user_agent || '') ? 'Windows' : /Macintosh|Mac OS/i.test(account.user_agent || '') ? 'macOS' : /Android/i.test(account.user_agent || '') ? 'Android' : /Linux/i.test(account.user_agent || '') ? 'Linux' : '未知'],
        ['浏览器版本', (account.user_agent || '').match(/(?:Chrome|Firefox|Version)\/([\d.]+)/)?.[0]],
        ['User Agent', account.user_agent],
      ]} />
      <Typography variant="caption" color="text.secondary">记录导入的浏览器资料。当前原版容器使用内置请求模拟，这些字段不会自动改变它的指纹。</Typography>
      {fields && <Stack component="form" spacing={2} onSubmit={submit}><Divider /><CopyField label="系统标签" value={fields.os_label || ''} onChange={change('os_label')} disabled={unavailable} notify={notify} helperText="例如 Win、Apple、Linux；保留你的原始标签。" /><CopyField label="User Agent" multiline minRows={3} value={fields.user_agent || ''} onChange={change('user_agent')} disabled={unavailable} notify={notify} /><Button type="submit" variant="contained" sx={{ alignSelf: 'flex-start' }} disabled={unavailable || !dirty}>保存修改</Button></Stack>}
    </Stack>}
    {confirmation && <Dialog open onClose={() => !busy && setConfirm(null)}><DialogTitle>{confirmation[0]}</DialogTitle><DialogContent><Typography>{confirmation[1]}</Typography></DialogContent><DialogActions><Button onClick={() => setConfirm(null)}>取消</Button><Button variant="contained" onClick={() => confirm === 'save' ? save() : act(confirm)}>确认</Button></DialogActions></Dialog>}
  </Stack>;
}
