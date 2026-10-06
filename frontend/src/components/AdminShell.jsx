import React, { useState } from 'react';
import { AppBar, Avatar, Box, Button, Chip, Divider, Drawer, List, ListItemButton, ListItemText, Stack, Toolbar, Typography } from '@mui/material';

export const modules = [
  ['overview', '工作台', 'OVERVIEW'], ['accounts', '账号管理', 'ACCOUNTS'],
  ['resources', '资源挂载', 'RESOURCES'], ['updates', '版本更新', 'UPDATES'], ['api', 'API 接入', 'API ACCESS'],
];
const width = 232;
export default function AdminShell({ page, navigate, refresh, logout, updatedAt, children }) {
  const [mobile, setMobile] = useState(false);
  const selected = modules.find(item => item[0] === page);
  const menu = <Box sx={{ p: 2, height: '100%', display: 'flex', flexDirection: 'column' }}>
    <Stack direction="row" spacing={1.5} alignItems="center" sx={{ px: 1, py: 2 }}><Avatar variant="rounded" sx={{ bgcolor: 'primary.main', fontWeight: 700 }}>C</Avatar><Box><Typography fontWeight={700}>ClewdR</Typography><Typography variant="caption" color="text.secondary">资源管理控制台</Typography></Box></Stack>
    <Divider sx={{ my: 2 }} /><Typography variant="overline" color="text.secondary" sx={{ px: 2 }}>管理空间</Typography>
    <List>{modules.map(([id, title, sub]) => <ListItemButton key={id} selected={page === id} onClick={() => { navigate(id); setMobile(false); }} sx={{ borderRadius: 2, mb: .75, '&.Mui-selected': { bgcolor: 'primary.light', color: 'primary.dark' } }}><ListItemText primary={title} secondary={sub} slotProps={{ primary: { fontSize: 14, fontWeight: 600 }, secondary: { fontSize: 10, letterSpacing: 1, mt: .5 } }} /></ListItemButton>)}</List>
    <Box sx={{ mt: 'auto', p: 2, bgcolor: 'background.default', borderRadius: 2 }}><Typography variant="caption" fontWeight={600}>官方镜像 · 独立容器</Typography><Typography variant="caption" color="text.secondary" display="block" sx={{ mt: .5 }}>上游源码保持原样</Typography></Box>
  </Box>;
  return <Box sx={{ display: 'flex', minHeight: '100vh' }}>
    <Drawer variant="permanent" sx={{ display: { xs: 'none', md: 'block' }, width, '& .MuiDrawer-paper': { width, borderRight: '1px solid', borderColor: 'divider' } }}>{menu}</Drawer>
    <Drawer open={mobile} onClose={() => setMobile(false)} sx={{ '& .MuiDrawer-paper': { width } }}>{menu}</Drawer>
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <AppBar position="sticky" color="inherit" elevation={0} sx={{ borderBottom: '1px solid', borderColor: 'divider', bgcolor: 'background.paper' }}><Toolbar sx={{ gap: 2 }}>
        <Button onClick={() => setMobile(true)} sx={{ display: { md: 'none' }, minWidth: 40 }}>菜单</Button>
        <Box sx={{ flex: 1 }}><Typography variant="body2" fontWeight={600}>{selected[1]}</Typography><Typography variant="caption" color="text.secondary">管理空间 / {selected[2]}</Typography></Box>
        <Chip label="管理员" size="small" variant="outlined" /><Button size="small" onClick={refresh}>刷新</Button><Button size="small" onClick={logout}>退出</Button>
      </Toolbar></AppBar>
      <Box component="main" sx={{ p: { xs: 2, md: 4 }, maxWidth: 1500, mx: 'auto' }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 3 }}><Box><Typography variant="h4" sx={{ fontSize: { xs: 26, md: 30 } }}>{selected[1]}</Typography><Typography variant="caption" color="text.secondary">自动刷新 · 最近同步 {updatedAt ? new Date(updatedAt).toLocaleTimeString() : '—'}</Typography></Box></Stack>
        {children}
      </Box>
    </Box>
  </Box>;
}
