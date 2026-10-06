import React, { useRef, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Card, CardContent, Stack, TextField, Typography } from '@mui/material';
import { parseAccountFile } from '../api.js';
import { toTimestamp } from '../accounts.js';
import ProfileFields from './ProfileFields.jsx';

const empty = { name: '', proxy: '', sessionKey: '', proxy_username: '', proxy_password: '', user_agent: '', os_label: '', email: '', email_password: '', expires: '', notes: '' };

export default function AccountForm({ api, refresh, notify }) {
  const [fields, setFields] = useState(empty);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false), [proxyResult, setProxyResult] = useState(null);
  const fileInput = useRef(null);
  const change = name => event => { setFields(current => ({ ...current, [name]: event.target.value })); if (name.startsWith('proxy')) setProxyResult(null); };
  async function testProxy() {
    setTesting(true); setProxyResult(null);
    try { setProxyResult(await api('/admin/proxy/test', 'POST', { proxy: fields.proxy, proxy_username: fields.proxy_username, proxy_password: fields.proxy_password })); }
    catch (error) { setProxyResult({ ok: false, message: error.message }); }
    finally { setTesting(false); }
  }
  async function importFile(event) {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 1024 * 1024) return notify('文件不能超过 1 MiB', 'error');
    setFields({ ...empty, ...parseAccountFile(await file.text(), file.name) });
    setProxyResult(null);
    notify('已提取文件头；其他站点 Cookie 不会上传。');
  }
  async function submit(event) {
    event.preventDefault(); setBusy(true);
    try {
      const { expires, ...values } = fields;
      await api('/admin/accounts', 'POST', { ...values, session_expires_at: toTimestamp(expires) });
      setFields(empty); setProxyResult(null); fileInput.current.value = '';
      notify('容器已挂载，请执行生成验证。'); await refresh();
    } catch (error) { notify(error.message, 'error'); }
    finally { setBusy(false); }
  }
  return <Card><CardContent sx={{ p: 3 }}><Stack spacing={2.5}>
    <Box><Typography variant="h6">新建账号容器</Typography><Typography color="text.secondary" variant="body2" sx={{ mt: 1 }}>填写专用代理和 sessionKey，自动创建独立容器与持久化目录。</Typography></Box>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ sm: 'center' }}>
      <Button component="label" variant="outlined" disabled={busy || testing}>导入账号 txt<input ref={fileInput} hidden type="file" accept=".txt,text/plain" onChange={importFile} /></Button>
      <Typography variant="caption" color="text.secondary">文件首行作为 sessionKey；可识别四段 SOCKS5 资料。</Typography>
    </Stack>
    <Stack component="form" onSubmit={submit} spacing={2.5}>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
        <TextField label="账号名称" required value={fields.name} onChange={change('name')} slotProps={{ htmlInput: { maxLength: 80 } }} disabled={busy} />
        <TextField label="代理地址" required value={fields.proxy} onChange={change('proxy')} placeholder="IP:端口 或 socks5://用户:密码@IP:端口" disabled={busy || testing} />
      </Box>
      <TextField label="sessionKey" type="password" required autoComplete="off" value={fields.sessionKey} onChange={change('sessionKey')} helperText="仅使用你自己的登录会话，提交后不会在容器列表中展示。" disabled={busy} />
      <ProfileFields {...{fields, change, notify}} disabled={busy} />
      <Accordion disableGutters elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: '8px !important', '&:before': { display: 'none' } }}>
        <AccordionSummary><Typography variant="body2">可选：代理认证与浏览器资料</Typography></AccordionSummary>
        <AccordionDetails><Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
          <TextField label="代理用户名" autoComplete="off" value={fields.proxy_username} onChange={change('proxy_username')} disabled={busy || testing} />
          <TextField label="代理密码" type="password" autoComplete="off" value={fields.proxy_password} onChange={change('proxy_password')} disabled={busy || testing} />
          <TextField label="UA（仅保存）" value={fields.user_agent} onChange={change('user_agent')} disabled={busy} />
          <TextField label="系统标签（仅保存）" value={fields.os_label} onChange={change('os_label')} disabled={busy} />
        </Box></AccordionDetails>
      </Accordion>
      <Stack spacing={1.5}>
        <Button variant="outlined" onClick={testProxy} disabled={busy || testing || !fields.proxy.trim()} sx={{ alignSelf: 'flex-start' }}>{testing ? '正在检测代理…' : '检测代理出口 IP'}</Button>
        {proxyResult && <Alert severity={proxyResult.ok ? 'success' : 'error'}>{proxyResult.ok ? `代理可用 · 出口 IP ${proxyResult.ip} · HTTPS 检测耗时 ${proxyResult.latency_ms} ms` : proxyResult.message}</Alert>}
        <Typography variant="caption" color="text.secondary">创建前会再次检测代理；检测通过表示 HTTPS 出口可用，账号生成资格需另行验证。</Typography>
      </Stack>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
        <Button type="submit" variant="contained" disabled={busy || testing || proxyResult?.ok === false}>{busy ? '正在检测并创建…' : '创建并挂载'}</Button>
        <Typography variant="caption" color="text.secondary">重试 5 · restricted 开启 · 两个 warning 关闭</Typography>
      </Stack>
    </Stack>
  </Stack></CardContent></Card>;
}
