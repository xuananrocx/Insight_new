import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { readFileSync } from 'node:fs'

// Python package version is the canonical release version on every platform.
const source = readFileSync(new URL('../src/__init__.py', import.meta.url), 'utf8')
const version = source.match(/^__version__\s*=\s*["']([^"']+)["']/m)?.[1]
if (!version) throw new Error('Cannot read canonical Insight version')
const packageVersion = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')).version
if (packageVersion !== version) throw new Error('Frontend package version differs from src/__init__.py')

export default defineConfig({
  plugins: [react()],
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(version),
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
  },
})
