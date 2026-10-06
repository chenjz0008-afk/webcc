import React, { useEffect, useState } from 'react';
import { Box, Button, Card, Chip, Dialog, DialogContent, DialogTitle, Drawer, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
import AccountDetails from '../components/AccountDetails.jsx';
import AccountForm from '../components/AccountForm.jsx';
import { expiration, formatDate, inactive, sortAccounts, statuses } from '../accounts.js';

export default function Accounts({ data, api, refresh, notify }) {
  const [open, setOpen] = useState(false), [search, setSearch] = useState(''), [filter, setFilter] = useState('all'), [selected, setSelected] = useState(null), [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60000); return () => clearInterval(timer); }, []);
  const accounts = sortAccounts(data.accounts.filter(a => (filter === 'all' || a.status === filter) && `${a.name} ${a.email || ''} ${a.proxy}`.toLowerCase().includes(search.toLowerCase())));
  const account = data.accounts.find(a => a.id === selected);
  const expiring = data.accounts.filter(a => !inactive(a) && a.session_expires_at && a.session_expires_at * 1000 > now && a.session_expires_at * 1000 - now < 7 * 86400000).length;
  const totalInactive = data.accounts.filter(inactive).length;
  return <Stack spacing={2.5}>
    <Stack direction="row" gap={1} flexWrap="wrap"><Chip size="small" variant="outlined" label={`${data.accounts.length - totalInactive} 个正常账号`} />{expiring > 0 && <Chip size="small" color="warning" variant="outlined" label={`${expiring} 个即将到期`} />}{totalInactive > 0 && <Chip size="small" variant="outlined" label={`${totalInactive} 个不可用`} />}</Stack>
    <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}><TextField label="搜索账号、邮箱或代理" value={search} onChange={e => setSearch(e.target.value)} /><TextField select label="状态" value={filter} onChange={e => setFilter(e.target.value)} sx={{ minWidth: 150, maxWidth: { sm: 190 } }}>{[['all','全部状态'], ...Object.entries(statuses).map(([v, [label]]) => [v, label])].map(([v,l]) => <MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField><Button variant="contained" onClick={() => setOpen(true)} sx={{ flexShrink: 0 }}>添加账号</Button></Stack>
    <TableContainer component={Card}><Table sx={{ minWidth: 980 }}><TableHead><TableRow>{['账号','状态','sessionKey 有效期','代理 / 出口','请求负载','操作'].map(v => <TableCell key={v} sx={{ whiteSpace: 'nowrap' }}>{v}</TableCell>)}</TableRow></TableHead><TableBody>
      {accounts.map(a => { const [label, color] = statuses[a.status] || [a.status,'default']; const check = a.proxy_check; const expiry = expiration(a, now); const muted = inactive(a); return <TableRow key={a.id} hover sx={{ bgcolor: muted ? 'action.hover' : undefined, '& td': { color: muted ? 'text.secondary' : undefined, py: 2.5 } }}>
        <TableCell><Typography fontWeight={600}>{a.name}</Typography><Typography variant="caption" color="text.secondary">{a.email || '邮箱未填写'}</Typography></TableCell>
        <TableCell><Chip size="small" label={label} color={color} variant="outlined" /><Typography variant="caption" color="text.secondary" display="block" sx={{ mt: .75 }}>{a.running ? '容器运行中' : '容器已停止'}</Typography></TableCell>
        <TableCell><Typography variant="body2" sx={{ fontWeight: muted ? 400 : 600, color: expiry.color }}>{expiry.label}</Typography><Typography variant="caption" color="text.secondary" display="block">{a.session_expires_at ? `${a.session_expiry_source === 'estimated' ? '预计 ' : ''}${formatDate(a.session_expires_at)}` : '补充 Cookie 的到期时间'}</Typography></TableCell>
        <TableCell sx={{ maxWidth: 240 }}><Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{a.proxy}</Typography><Typography variant="caption" color="text.secondary" display="block">{check?.ok ? `出口 ${check.ip} · ${check.latency_ms} ms` : check ? '代理检测失败' : '出口尚未检测'}</Typography></TableCell>
        <TableCell>{a.inflight} / 1<Typography variant="caption" color="text.secondary" display="block">已分配 {a.dispatches || 0} 次</Typography></TableCell>
        <TableCell><Button onClick={() => setSelected(a.id)} sx={{ whiteSpace: 'nowrap' }}>查看详情</Button></TableCell>
      </TableRow>; })}
      {!accounts.length && <TableRow><TableCell colSpan={6} align="center" sx={{ py: 6 }}>没有匹配的账号</TableCell></TableRow>}
    </TableBody></Table></TableContainer>
    <Typography variant="caption" color="text.secondary">显示 {accounts.length} / {data.accounts.length} 个账号 · 到期时间最近的优先，不可用账号置于底部</Typography>
    <Drawer anchor="right" open={Boolean(account)} onClose={() => setSelected(null)} slotProps={{ paper: { sx: { width: { xs: '100%', sm: 600 } } } }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 3, py: 2, flexShrink: 0, bgcolor: 'background.paper', borderBottom: 1, borderColor: 'divider' }}><Typography variant="subtitle1" fontWeight={600}>账号详情</Typography><Button onClick={() => setSelected(null)}>关闭</Button></Stack>
      <Box sx={{ p: 3, flex: 1, minHeight: 0, overflowY: 'auto' }}>{account && <AccountDetails key={account.id} {...{account, api, refresh, notify, now}} />}</Box>
    </Drawer>
    <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="md"><DialogTitle><Stack direction="row" justifyContent="space-between" alignItems="center"><span>添加账号</span><Button onClick={() => setOpen(false)}>关闭</Button></Stack></DialogTitle><DialogContent><AccountForm {...{api, notify}} refresh={async () => { await refresh(); setOpen(false); }} /></DialogContent></Dialog>
  </Stack>;
}
