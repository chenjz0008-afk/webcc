import React from 'react';
import { Box } from '@mui/material';
import SecretField from './SecretField.jsx';
import CopyField from './CopyField.jsx';
export default function ProfileFields({ fields, change, disabled, notify }) {
  return <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
    <CopyField label="邮箱" value={fields.email || ''} onChange={change('email')} disabled={disabled} notify={notify} autoComplete="off" slotProps={{ htmlInput: { maxLength: 254 } }} />
    <SecretField label="邮箱密码" value={fields.email_password} onChange={change('email_password')} disabled={disabled} copy notify={notify} />
    <CopyField label="sessionKey 到期时间" type="datetime-local" value={fields.expires || ''} onChange={change('expires')} disabled={disabled} notify={notify} slotProps={{ inputLabel: { shrink: true } }} helperText="可填写浏览器 Cookie 的 Expires；新账号留空按导入时间加 29 天估算。按本机时区显示。" sx={{ gridColumn: '1 / -1' }} />
    <CopyField label="备注" multiline minRows={2} value={fields.notes || ''} onChange={change('notes')} disabled={disabled} notify={notify} slotProps={{ htmlInput: { maxLength: 2000 } }} sx={{ gridColumn: '1 / -1' }} />
  </Box>;
}
