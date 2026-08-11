import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/qa': 'http://localhost:8000',
      '/concept': 'http://localhost:8000',
    },
  },
})
