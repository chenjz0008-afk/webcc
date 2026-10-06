import React, { useState } from 'react';
import { Button, Card, CardContent, Stack, TextField, Typography } from '@mui/material';

export default function Login({ onLogin, notify }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault(); setBusy(true);
    try { await onLogin(password); setPassword(''); }
    catch (error) { notify(error.message, 'error'); }
    finally { setBusy(false); }
  }
  return <Card sx={{ maxWidth: 520 }}><CardContent sx={{ p: 3 }}>
    <Stack component="form" onSubmit={submit} spacing={2.5}>
      <Typography variant="h6">管理员登录</Typography>
      <Typography color="text.secondary" variant="body2">使用现有管理员密码。登录凭据仅保留在当前页面内存，刷新后需重新登录。</Typography>
      <TextField label="管理员密码" type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} />
      <Button variant="contained" type="submit" disabled={busy}>{busy ? '正在登录…' : '登录管理台'}</Button>
    </Stack>
  </CardContent></Card>;
}
