import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

// Renderer bundle only (main/preload are compiled by tsc). Served by the app's own privileged
// protocol (pef-app://monitor/), so relative asset paths and no inline scripts (strict CSP).
export default defineConfig({
  root: resolve(__dirname, 'src/renderer'),
  base: './',
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, 'dist/renderer'),
    emptyOutDir: true,
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
    sourcemap: false,
    target: 'chrome130',
  },
  test: {
    root: __dirname,
    include: ['tests/**/*.test.ts'],
    environment: 'node',
    testTimeout: 15000,
  },
});
