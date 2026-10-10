import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { outDir: '../backend/static', emptyOutDir: true },
  server: { host: '127.0.0.1', proxy: { '/admin': 'http://127.0.0.1:9000' } },
});
