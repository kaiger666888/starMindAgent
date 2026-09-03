#!/usr/bin/env bash
# starMindAgent 后端启动脚本（固化 LLM 网关选择）
#
# LLM 后端路由（default_backend()）：
#   LLM_BACKEND=openai + LLM_BASE_URL -> OpenAICompatibleBackend（higress 网关，默认）
#   未配上述 -> ANTHROPIC_BASE_URL 存在时走 AnthropicBackend（ai-nexus 网关）
#
# 实测对比（2026-08-27，见 memory）：
#   higress + thinking disabled : TTFT ~2s，  单次回答 ~10s
#   ai-nexus + glm-5.3         : TTFT 11-38s，单次回答 ~28s
set -e
cd "$(dirname "$0")"

export LLM_BACKEND=openai
# 注意：OpenAICompatibleBackend 拼 URL 为 {base}/chat/completions，
# 所以 base 必须含 /v1（实际端点 /gateway/v1/chat/completions）
export LLM_BASE_URL=https://higress.devops.ecp.digitalvolvo.com/gateway/v1
export LLM_API_KEY=4375b0f1-b3a6-46f9-9421-e1897c12aca8
export LLM_MODEL=glm

# A2 代码库 grounding（app/search/codebase.py rg 检索）：
# QA 问题含代码标识符时预检索真实代码片段注入 prompt。
# 置空 = 关闭（零影响）。当前指向本仓库自身（自举学习场景）。
export CODEBASE_DIR="C:/Kais_Projects/Git_Projects/Github/starMindAgent/growth-agent-integrated/backend/app"

# 清 pycache（无 --reload 模式下改代码必须清，否则旧字节码可能残留）
find app -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
