#!/usr/bin/env bash
# starMindAgent 开发测试栈：独立端口 + 独立数据库，与生产实例完全隔离。
#
#   生产（用户在用）  ：后端 :8000 + 前端 :5166 + 库 growth_agent      （start_all.sh）
#   测试（开发验证用）：后端 :8100 + 前端 :5266 + 库 growth_agent_test（本脚本）
#
# 用途：开发/测试期间的代码改动、重启、造删测试数据都只影响测试栈，
# 用户使用生产实例不被打断。前端在 :5266 验证认可后，再重启 :8000 生产后端上线。
#
# 幂等：端口已在监听则跳过；测试库/表结构已就绪则跳过初始化。
#
# 数据隔离说明：两个库同在 growth-agent-db-1 容器（零额外资源），
# 测试库数据随便造随便删，不影响生产档案视图。

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
exec >> /tmp/starMind_test_stack.log 2>&1

ROOT="$(cd "$(dirname "$0")" && pwd)"
DB_CONTAINER=growth-agent-db-1
TEST_DB=growth_agent_test
BACKEND_PORT=8100
FRONTEND_PORT=5266

# -- 1. DB 容器就绪（生产栈通常已拉起；单独使用本脚本时兜底等待） --
for i in $(seq 1 12); do
  if docker ps 2>/dev/null | grep -q "$DB_CONTAINER"; then break; fi
  if docker info >/dev/null 2>&1; then
    docker start "$DB_CONTAINER" >/dev/null 2>&1 && break
  fi
  log "等待 Docker 引擎... ($i/12)"
  sleep 10
done

# -- 2. 测试库 + 表结构（幂等：库存在则跳过建库；qa_session 表存在则跳过迁移） --
if ! docker exec "$DB_CONTAINER" psql -U dev -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='$TEST_DB'" | grep -q 1; then
  docker exec "$DB_CONTAINER" psql -U dev -d postgres -c "CREATE DATABASE $TEST_DB"
  log "测试库 $TEST_DB 已创建"
else
  log "测试库 $TEST_DB 已存在"
fi

if ! docker exec "$DB_CONTAINER" psql -U dev -d "$TEST_DB" -tAc \
     "SELECT 1 FROM information_schema.tables WHERE table_name='qa_session'" | grep -q 1; then
  for sql in "$ROOT"/growth-agent-integrated/backend/migrations/0*.sql; do
    docker exec -i "$DB_CONTAINER" psql -U dev -d "$TEST_DB" -v ON_ERROR_STOP=1 -f - < "$sql" \
      && log "迁移已应用: $(basename "$sql")" \
      || { log "迁移失败: $sql"; exit 1; }
  done
  log "测试库表结构初始化完成"
else
  log "测试库表结构已就绪，跳过迁移"
fi

# 端口占用检查：必须词边界匹配（":5266.*" 会误命中 :5266xx 这类长端口）
listening() { netstat -ano | grep "LISTENING" | grep -qE "[:.]$1\b"; }

# -- 3. 测试后端 :8100（测试库；带 --reload，改代码自动重启不影响生产） --
if listening $BACKEND_PORT; then
  log "测试后端 :$BACKEND_PORT 已在监听，跳过"
else
  export LLM_BACKEND=openai
  export LLM_BASE_URL=https://higress.devops.ecp.digitalvolvo.com/gateway/v1
  export LLM_API_KEY=4375b0f1-b3a6-46f9-9421-e1897c12aca8
  export LLM_MODEL=glm
  export DATABASE_URL="postgresql+asyncpg://dev:dev@localhost:5432/$TEST_DB"
  cd "$ROOT/growth-agent-integrated/backend"
  find app -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
  nohup python -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload \
    > /tmp/starMind_test_backend.log 2>&1 &
  log "测试后端启动中 (库 $TEST_DB) -> /tmp/starMind_test_backend.log"
fi

# -- 4. 测试前端 :5266（proxy -> :8100） --
if listening $FRONTEND_PORT; then
  log "测试前端 :$FRONTEND_PORT 已在监听，跳过"
else
  cd "$ROOT/growth-agent-integrated/frontend"
  PORT="$FRONTEND_PORT" BACKEND_PORT="$BACKEND_PORT" \
    nohup npx vite > /tmp/starMind_test_frontend.log 2>&1 &
  log "测试前端启动中 -> /tmp/starMind_test_frontend.log"
fi

log "完成。测试前端 http://localhost:$FRONTEND_PORT （生产 :5166 不受影响）"
