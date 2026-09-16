import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// 构建产物直接放进 Python 包里，`devpanel serve` 用 FastAPI 静态服务。
// 开发时 `npm run dev` 起在 5174，/api 代理到面板的 9000。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../src/devpanel/web/dist',
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:9000', changeOrigin: true },
    },
  },
})
