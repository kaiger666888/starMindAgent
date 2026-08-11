#!/usr/bin/env bash
# 一键启动：后端 FastAPI + 前端 dev server（真实推理不可用时 StubInferenceSession 兜底）
set -e
cd "$(dirname "$0")"
echo "== 后端 =="
( cd backend && pip install -q -r requirements.txt 2>/dev/null || true )
( cd backend && uvicorn app.main:app --reload --port 8000 ) &
BACK_PID=$!
echo "== 前端 =="
( cd frontend && npm install --silent 2>/dev/null || true )
( cd frontend && npm run dev ) &
FRONT_PID=$!
trap "kill $BACK_PID $FRONT_PID 2>/dev/null" EXIT
echo "后端: http://localhost:8000  前端: http://localhost:5173"
echo "可观测: http://localhost:8000/harness/obs/metrics"
wait
