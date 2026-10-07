import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, InputLabel, MenuItem, Select, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import SecretField from '../components/SecretField.jsx';

const permissions = {messages: '消息调用', models: '模型列表', experimental_tools: '实验工具'};
const initial = {name: '', accounts: [], scopes: ['messages', 'models'], rpm: 60, expiry: ''};
export default function ApiKeys({api, data, notify}) {
  const [keys, setKeys] = useState([]), [open, setOpen] = useState(false);
  const [form, setForm] = useState(initial), [created, setCreated] = useState(null), [busy, setBusy] = useState(false);
  const reload = useCallback(async () => {
    try { setKeys((await api('/admin/api-keys')).keys); } catch (error) { notify(error.message, 'error'); }
  }, [api, notify]);
  useEffect(() => { reload(); }, [reload]);
  const accounts = data.accounts || [];
  const accountName = id => accounts.find(a => a.id === id)?.name || id;
  const change = (name, value) => setForm(current => ({...current, [name]: value}));
  async function create() {
    setBusy(true);
    try {
      const result = await api('/admin/api-keys', 'POST', {name: form.name, accounts: form.accounts, scopes: form.scopes,
        rpm: Number(form.rpm), expires_at: form.expiry ? Math.floor(new Date(form.expiry).getTime() / 1000) : null});
      setCreated(result); setOpen(false); setForm(initial); await reload();
    } catch (error) { notify(error.message, 'error'); }
    finally { setBusy(false); }
  }
  async function revoke(key) {
    setBusy(true);
    try { await api(`/admin/api-keys/${key.id}/revoke`, 'POST', {}); await reload(); notify('密钥已撤销'); }
    catch (error) { notify(error.message, 'error'); }
    finally { setBusy(false); }
  }
  return <Stack spacing={3}>
    <Alert severity="info">为不同调用者分配独立密钥、账号范围和请求速率。密钥不能访问管理接口；撤销后新请求和未获得资源的排队请求会被拒绝。</Alert>
    <Card><CardContent><Stack direction="row" alignItems="center" justifyContent="space-between" sx={{mb: 2}}>
      <Typography variant="h6">调用密钥</Typography><Stack direction="row" spacing={1}><Button onClick={reload} disabled={busy}>刷新</Button><Button variant="contained" onClick={() => setOpen(true)} disabled={busy}>创建密钥</Button></Stack>
    </Stack><Box sx={{overflowX: 'auto'}}><Table><TableHead><TableRow>{['名称', '状态', '账号范围', '功能权限', 'RPM', '到期时间', '操作'].map(label => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead>
      <TableBody>{keys.map(key => {
        const expired = key.expires_at && key.expires_at * 1000 <= Date.now();
        return <TableRow key={key.id}><TableCell><Typography variant="body2">{key.name}</Typography><Typography variant="caption" color="text.secondary">{key.id}</Typography></TableCell>
          <TableCell><Chip size="small" label={key.revoked ? '已撤销' : expired ? '已到期' : '可用'} color={key.revoked || expired ? 'default' : 'success'} /></TableCell>
          <TableCell sx={{maxWidth: 260}}>{key.accounts.map(accountName).join('、')}</TableCell><TableCell>{key.scopes.map(scope => permissions[scope]).join('、')}</TableCell>
          <TableCell>{key.rpm}</TableCell><TableCell sx={{whiteSpace: 'nowrap'}}>{key.expires_at ? new Date(key.expires_at * 1000).toLocaleString() : '不限'}</TableCell>
          <TableCell><Button color="error" disabled={key.revoked || busy} onClick={() => revoke(key)}>撤销</Button></TableCell></TableRow>;
      })}{!keys.length && <TableRow><TableCell colSpan={7} align="center" sx={{py: 5}}>尚未创建独立调用密钥</TableCell></TableRow>}</TableBody></Table></Box></CardContent></Card>
    <Dialog open={open} onClose={() => { if (!busy) setOpen(false); }} fullWidth maxWidth="sm"><DialogTitle>创建调用密钥</DialogTitle><DialogContent><Stack spacing={2} sx={{pt: 1}}>
      <TextField label="名称" value={form.name} onChange={event => change('name', event.target.value)} slotProps={{htmlInput: {maxLength: 80}}} />
      <FormControl><InputLabel>账号范围</InputLabel><Select multiple label="账号范围" value={form.accounts} onChange={event => change('accounts', event.target.value)}>{accounts.map(account => <MenuItem key={account.id} value={account.id}>{account.name}</MenuItem>)}</Select></FormControl>
      <FormControl><InputLabel>功能权限</InputLabel><Select multiple label="功能权限" value={form.scopes} onChange={event => change('scopes', event.target.value)}>{Object.entries(permissions).map(([scope, label]) => <MenuItem key={scope} value={scope}>{label}</MenuItem>)}</Select></FormControl>
      <TextField label="每分钟最多请求数" type="number" value={form.rpm} onChange={event => change('rpm', event.target.value)} slotProps={{htmlInput: {min: 1, max: 1000}}} />
      <TextField label="到期时间（可选）" type="datetime-local" value={form.expiry} onChange={event => change('expiry', event.target.value)} slotProps={{inputLabel: {shrink: true}}} />
      <Typography variant="caption" color="text.secondary">实验工具权限只允许调用候选实验入口，不代表生产已开启该能力。</Typography>
    </Stack></DialogContent><DialogActions><Button onClick={() => setOpen(false)} disabled={busy}>取消</Button><Button variant="contained" onClick={create} disabled={busy || !form.name.trim() || !form.accounts.length || !form.scopes.length}>创建</Button></DialogActions></Dialog>
    <Dialog open={Boolean(created)} onClose={() => setCreated(null)} fullWidth maxWidth="sm"><DialogTitle>保存调用密钥</DialogTitle><DialogContent><Stack spacing={2} sx={{pt: 1}}><Alert severity="info">完整密钥仅在创建时显示，关闭后无法再次读取。</Alert><SecretField label="API Key" value={created?.key} readOnly copy notify={notify} fullWidth /></Stack></DialogContent><DialogActions><Button onClick={() => setCreated(null)}>完成</Button></DialogActions></Dialog>
  </Stack>;
}
