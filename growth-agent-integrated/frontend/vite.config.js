import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 生产实例：:5166 -> proxy :8000（start_all.sh / 计划任务用这套默认值）
// 测试实例：PORT=5266 BACKEND_PORT=8100（dev_test_stack.sh 注入，
//           指向测试库 growth_agent_test 的独立后端，与生产完全隔离）
const PORT = Number(process.env.PORT || 5166)
const BACKEND_PORT = process.env.BACKEND_PORT || 8000
const backend = `http://localhost:${BACKEND_PORT}`

export default defineConfig({
  plugins: [react()],
  server: {
    port: PORT,
    strictPort: true,
    proxy: {
      '/qa': backend,
      '/concept': backend,
      '/memory': backend,
      '/harness': backend,
      '/learning': backend,
    },
  },
})
