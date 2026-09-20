import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // amazon-cognito-identity-js is published as a browserify-era bundle and
  // touches `global` at import time. Vite defines no such binding, so without
  // this the console renders a blank page — in the production build as well
  // as in dev, because the reference is at runtime, not build time.
  define: { global: 'globalThis' },
  // Pinned, and strict. The dev origin is registered in Auth0 as an exact
  // callback URL, so a port that silently shifts to 5174 because 5173 was
  // busy would fail login with a callback-mismatch error that reads like a
  // dashboard misconfiguration.
  server: { port: 5173, strictPort: true },
  preview: { port: 4173, strictPort: true },
  build: { outDir: 'dist', sourcemap: false },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
  },
});
