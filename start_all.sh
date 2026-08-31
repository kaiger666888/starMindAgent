#!/usr/bin/env bash
# starMindAgent 全栈启动脚本：DB 容器(等就绪) -> 后端 :8000 -> 前端 :5166
# 供任务计划程序开机自启调用，也可手动执行。
# 幂等：已在监听的端口直接跳过，不重复起进程。

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# 日志统一落文件（计划任务调 stdout 会被丢弃，脚本内部重定向保证可追溯）
exec >> /tmp/starMind_autostart.log 2>&1

ROOT="$(cd "$(dirname "$0")" && pwd)"

# -- 1. DB 容器：Docker 引擎就绪后启动（开机时 Docker Desktop 可能还在启动） --
for i in $(seq 1 12); do
  if docker ps 2>/dev/null | grep -q growth-agent-db-1; then
    log "DB 容器已在运行"
    break
  fi
  if docker info >/dev/null 2>&1; then
    docker start growth-agent-db-1 >/dev/null 2>&1 && log "DB 容器已启动" && break
  fi
  log "等待 Docker 引擎... ($i/12)"
  sleep 10
done

# 端口占用检查：必须词边界匹配（":8000.*" 会误命中 :80001 这类长端口）
listening() { netstat -ano | grep "LISTENING" | grep -qE "[:.]$1\b"; }

# -- 2. 后端 :8000 --
if listening 8000; then
  log "后端 :8000 已在监听，跳过"
else
  # LLM 网关选择固化（与 start_backend.sh 一致）：higress openai 兼容
  export LLM_BACKEND=openai
  export LLM_BASE_URL=https://higress.devops.ecp.digitalvolvo.com/gateway/v1
  export LLM_API_KEY=4375b0f1-b3a6-46f9-9421-e1897c12aca8
  export LLM_MODEL=glm
  cd "$ROOT/growth-agent-integrated/backend"
  nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    > /tmp/starMind_backend.log 2>&1 &
  log "后端启动中 -> /tmp/starMind_backend.log"
fi

# -- 3. 前端 :5166 --
if listening 5166; then
  log "前端 :5166 已在监听，跳过"
else
  cd "$ROOT/growth-agent-integrated/frontend"
  nohup npx vite --port 5166 > /tmp/starMind_frontend.log 2>&1 &
  log "前端启动中 -> /tmp/starMind_frontend.log"
fi

log "完成。前端 http://localhost:5166"
