import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Сборка лендинга: бандл в backend/static/landing/, затем scripts/prerender.mjs
// дописывает пререндеренные index.html (ky) и ru/index.html (ru).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: path.resolve(__dirname, '../backend/static/landing'),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
    }
  },
  base: '/static/landing/',
})
