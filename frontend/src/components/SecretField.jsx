import React, { useState } from 'react';
import { Button, InputAdornment, TextField } from '@mui/material';
import { copyText } from '../accounts.js';
export default function SecretField({ value, copy = false, notify, readOnly, ...props }) {
  const [visible, setVisible] = useState(false);
  return <TextField {...props} value={value || ''} type={visible ? 'text' : 'password'} autoComplete="off" slotProps={{
    input: { readOnly, endAdornment: <InputAdornment position="end"><Button size="small" disabled={props.disabled} onClick={() => setVisible(!visible)}>{visible ? '隐藏' : '显示'}</Button>{copy && <Button size="small" aria-label={`复制${props.label}`} disabled={!value || props.disabled} onClick={() => copyText(value, notify)}>复制</Button>}</InputAdornment> },
  }} />;
}
