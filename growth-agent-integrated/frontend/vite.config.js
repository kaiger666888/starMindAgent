import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5166,
    strictPort: true,
    proxy: {
      '/qa': 'http://localhost:8000',
      '/concept': 'http://localhost:8000',
      '/memory': 'http://localhost:8000',
      '/harness': 'http://localhost:8000',
      '/learning': 'http://localhost:8000',
    },
  },
})
