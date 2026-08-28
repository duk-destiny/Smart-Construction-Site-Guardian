/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 离线约束：构建产物零 CDN 引用，全部本地打包，由 FastAPI 托管（api/main.py）
export default defineConfig({
  plugins: [react()],
  server: {
    // 开发模式：API 走本机 FastAPI（uvicorn api.main:app --port 8000）；
    // CORS 需在 API 侧开 API_DEV_CORS=1（或 config.api.dev_cors）
    proxy: { '/api': 'http://localhost:8000' },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 2048 },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
})
