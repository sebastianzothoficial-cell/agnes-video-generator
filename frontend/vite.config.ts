import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Local/Docker builds keep the backend-served static/ layout.
// On Vercel, frontend/ is an independent service, so Vite must emit dist/
// and use root-relative asset URLs.
export default defineConfig({
  plugins: [vue()],
  base: process.env.VERCEL ? '/' : '/static/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: process.env.VERCEL ? 'dist' : '../static',
    emptyOutDir: process.env.VERCEL ? true : false,
    assetsDir: 'assets',
    sourcemap: false,
  },
  server: {
    host: '0.0.0.0',
    allowedHosts: true,
    port: 5173,
    // 开发模式：API 请求转发到后端（需 python server.py 已在 8765 运行）
    proxy: {
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },
})
