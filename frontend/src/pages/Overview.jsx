import React from 'react';
import { Alert, Box, Button, Card, CardContent, LinearProgress, Stack, Typography } from '@mui/material';
export default function Overview({ data, navigate }) {
  const ready = data.accounts.filter(a => a.status === 'ready' && a.running).length;
  const issues = data.accounts.filter(a => ['quarantined', 'error', 'expired'].includes(a.status));
  const capacity = Math.min(ready, data.limits.total);
  const stats = [['资源总数', data.accounts.length], ['可调度资源', ready], ['进行中请求', data.inflight], ['等待请求', data.waiting]];
  return <Stack spacing={3}>
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', lg: 'repeat(4,1fr)' }, gap: 2 }}>{stats.map(([name, value]) => <Card key={name}><CardContent><Typography variant="body2" color="text.secondary">{name}</Typography><Typography variant="h4" sx={{ my: 1 }}>{value}</Typography></CardContent></Card>)}</Box>
    {issues.length > 0 && <Alert severity="warning" action={<Button onClick={() => navigate('accounts')}>查看</Button>}>{issues.length} 个资源需要处理，已停止分配新请求。</Alert>}
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1.4fr 1fr' }, gap: 3 }}>
      <Card><CardContent sx={{ p: 3 }}><Stack spacing={2}><Typography variant="h6">资源调度</Typography><Typography variant="body2" color="text.secondary">优先空闲资源，同等负载选择最久未使用账号；占用与选择在同一锁内完成。</Typography><LinearProgress variant="determinate" value={capacity ? Math.min(100, data.inflight / capacity * 100) : 0} sx={{ height: 8, borderRadius: 4 }} /><Typography variant="body2">有效并发 {capacity} · 每账号 1 · 全局上限 {data.limits.total}</Typography><Typography variant="body2" color="text.secondary">等待队列 {data.waiting} / {data.limits.queue}，最长等待 {data.limits.queue_seconds} 秒。满载时排队，异常账号保持隔离。</Typography><Button variant="outlined" sx={{ alignSelf: 'flex-start' }} onClick={() => navigate('resources')}>查看已挂载资源</Button></Stack></CardContent></Card>
      <Card><CardContent sx={{ p: 3 }}><Stack spacing={2}><Typography variant="h6">版本状态</Typography><Typography variant="body2">{data.update.message || '尚未检测版本'}</Typography><Typography variant="body2" color="text.secondary">自动检测：每 {Math.round(data.settings.update_seconds / 3600)} 小时一次</Typography><Typography variant="caption" color="text.secondary">已部署提交：{data.update.deployed_sha?.slice(0, 12) || '初始镜像'}</Typography><Button sx={{ alignSelf: 'flex-start' }} onClick={() => navigate('updates')}>管理版本更新</Button></Stack></CardContent></Card>
    </Box>
    <Alert severity="info">可调度状态代表容器就绪；账号资格和额度以实际生成验证为准。调度次数统计从本次管理器启动开始。</Alert>
  </Stack>;
}
