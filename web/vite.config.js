import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // amazon-cognito-identity-js is published as a browserify-era bundle and
  // touches `global` at import time. Vite defines no such binding, so without
  // this the console renders a blank page — in the production build as well
  // as in dev, because the reference is at runtime, not build time.
  define: { global: 'globalThis' },
  build: { outDir: 'dist', sourcemap: false },
});
