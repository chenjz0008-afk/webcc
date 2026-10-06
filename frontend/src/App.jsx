import React, { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react';
import { Alert, Box, LinearProgress, Snackbar, Stack, Typography } from '@mui/material';
import { createApi } from './api.js';
import Login from './components/Login.jsx';
import AdminShell, { modules } from './components/AdminShell.jsx';
const Overview = lazy(() => import('./pages/Overview.jsx'));
const Accounts = lazy(() => import('./pages/Accounts.jsx'));
const Resources = lazy(() => import('./pages/Resources.jsx'));
const Updates = lazy(() => import('./pages/Updates.jsx'));
const ApiAccess = lazy(() => import('./pages/ApiAccess.jsx'));

const currentPage = () => modules.some(m => m[0] === location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
export default function App() {
  const [key, setKey] = useState(''), [data, setData] = useState(null), [message, setMessage] = useState(null);
  const [page, setPage] = useState(currentPage), [updatedAt, setUpdatedAt] = useState(null);
  const refreshing = useRef(false), generation = useRef(0);
  const api = useMemo(() => createApi(key), [key]);
  const notify = useCallback((text, severity = 'success') => setMessage({ text, severity }), []);
  const logout = useCallback(() => { generation.current++; setKey(''); setData(null); }, []);
  const refresh = useCallback(async () => {
    if (!key || refreshing.current) return;
    refreshing.current = true; const requestGeneration = generation.current;
    try { const snapshot = await api('/admin/accounts'); if (requestGeneration === generation.current) { setData(snapshot); setUpdatedAt(Date.now()); } }
    catch (error) { if (requestGeneration === generation.current) { if (error.status === 401) logout(); notify(error.message, 'error'); } }
    finally { refreshing.current = false; }
  }, [api, key, logout, notify]);
  useEffect(() => { if (!key) return; const timer = setInterval(refresh, 10000); return () => clearInterval(timer); }, [key, refresh]);
  useEffect(() => { const change = () => setPage(currentPage()); window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change); }, []);
  function navigate(id) { location.hash = id; setPage(id); }
  async function login(password) { const snapshot = await createApi(password)('/admin/accounts'); generation.current++; setKey(password); setData(snapshot); setUpdatedAt(Date.now()); }
  const props = { data, api, refresh, notify, navigate };
  return <>
    {!key || !data ? <Box sx={{ minHeight:'100vh', display:'grid',placeItems:'center',p:3 }}><Stack spacing={3} sx={{ width:'100%',maxWidth:520 }}><Box><Typography variant="overline" color="primary" sx={{ letterSpacing:3 }}>CLEWDR CONSOLE</Typography><Typography variant="h4">统一资源管理</Typography><Typography variant="body2" color="text.secondary" sx={{ mt:1 }}>账号、容器、代理和版本状态，集中管理。</Typography></Box><Login onLogin={login} notify={notify} /><Typography variant="caption" color="text.secondary">会话仅保存在当前页面内存 · 官方镜像 · 独立资源</Typography></Stack></Box> :
      <AdminShell {...{page,navigate,refresh,logout,updatedAt}}>
        <Suspense fallback={<LinearProgress />} >
        {page === 'overview' && <Overview {...props} />}
        {page === 'accounts' && <Accounts {...props} />}
        {page === 'resources' && <Resources {...props} />}
        {page === 'updates' && <Updates {...props} />}
        {page === 'api' && <ApiAccess />}
        </Suspense>
      </AdminShell>}
    <Snackbar open={Boolean(message)} autoHideDuration={6000} onClose={() => setMessage(null)} anchorOrigin={{vertical:'bottom',horizontal:'center'}}><Alert severity={message?.severity || 'success'} variant="filled" onClose={() => setMessage(null)}>{message?.text}</Alert></Snackbar>
  </>;
}
