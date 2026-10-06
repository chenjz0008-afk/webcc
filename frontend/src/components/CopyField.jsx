import React from 'react';
import { Button, InputAdornment, TextField } from '@mui/material';
import { copyText } from '../accounts.js';
export default function CopyField({ value, notify, slotProps = {}, ...props }) {
  return <TextField {...props} value={value || ''} slotProps={{ ...slotProps, input: { ...slotProps.input, endAdornment: <InputAdornment position="end"><Button size="small" aria-label={`复制${props.label}`} disabled={!value} onClick={() => copyText(value, notify)}>复制</Button></InputAdornment> } }} />;
}
