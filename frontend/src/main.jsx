import React from 'react';
import { createRoot } from 'react-dom/client';
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material';
import App from './App.jsx';

const theme = createTheme({
  palette: { primary: { main: '#1976d2', light: '#e3f2fd', dark: '#115293' }, background: { default: '#f6f8fc', paper: '#ffffff' }, text: { primary: '#1e293b', secondary: '#64748b' } },
  typography: { fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', h4: { fontWeight: 700, letterSpacing: '-0.8px' }, h6: { fontWeight: 600 } },
  shape: { borderRadius: 12 },
  components: {
    MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { textTransform: 'none', borderRadius: 8 } } },
    MuiCard: { defaultProps: { variant: 'outlined' }, styleOverrides: { root: { borderColor: '#e4e9ed' } } },
    MuiTextField: { defaultProps: { size: 'small', fullWidth: true } },
  },
});

createRoot(document.getElementById('root')).render(<ThemeProvider theme={theme}><CssBaseline /><App /></ThemeProvider>);
