import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把 /api 与 /health 代理到 FastAPI 后端（backend/ 下，默认 8000 端口）
// 生产分开部署：构建时通过 VITE_API_BASE_URL 注入后端基地址（跨域）；
// 未设置则为空（同源部署，如 nginx 反代前后端同域），见 src/lib/api.js
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: true, // 监听 0.0.0.0，CloudStudio 等代理环境可访问
    // CloudStudio 动态代理域名，允许任意主机头（仅开发环境）
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
