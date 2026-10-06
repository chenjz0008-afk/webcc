export const statuses = {
  ready: ['可用', 'success'], pending: ['创建中', 'info'], updating: ['更新中', 'info'],
  paused: ['已暂停', 'default'], quarantined: ['已隔离', 'default'], error: ['异常', 'default'],
  disabled: ['已停用', 'default'], expired: ['已到期', 'default'],
};
export const inactive = account => ['paused', 'quarantined', 'error', 'disabled', 'expired'].includes(account.status);
export const formatDate = value => value ? new Date(value * 1000).toLocaleString() : '未填写';
export const toLocalDate = value => {
  if (!value) return '';
  const date = new Date(value * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
export const toTimestamp = value => value ? Math.floor(new Date(value).getTime() / 1000) : null;
export async function copyText(value, notify) {
  try { await navigator.clipboard.writeText(String(value ?? '')); notify?.('已复制'); }
  catch { notify?.('复制失败，请手动复制', 'error'); }
}
export function expiration(account, now = Date.now()) {
  if (inactive(account)) return { label: statuses[account.status]?.[0] || '不可用', color: 'text.secondary' };
  if (!account.session_expires_at) return { label: '到期时间未知', color: 'text.secondary' };
  const minutes = Math.ceil((account.session_expires_at * 1000 - now) / 60000);
  if (minutes <= 0) return { label: '已到期', color: 'text.secondary' };
  const days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60);
  const left = days ? `${days} 天 ${hours} 小时` : hours ? `${hours} 小时 ${minutes % 60} 分钟` : `${minutes} 分钟`;
  return { label: `${account.session_expiry_source === 'estimated' ? '预计' : ''}剩余 ${left}`, color: minutes < 4320 ? 'error.main' : minutes < 10080 ? 'warning.main' : 'text.primary' };
}
export const sortAccounts = accounts => [...accounts].sort((a, b) => Number(inactive(a)) - Number(inactive(b)) ||
  (inactive(a) ? a.name.localeCompare(b.name) : (a.session_expires_at || Infinity) - (b.session_expires_at || Infinity)) || a.name.localeCompare(b.name));
