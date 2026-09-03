#!/usr/bin/env bash
# A1 管道：用 claude headless 模式把代码库消化成结构化 markdown，
# 然后 POST 到 sMA 后端 /learning/import 建学习材料（L0 根 QAStep + 概念抽取）。
#
# 用法:
#   ./ingest_repo.sh <repo_path> [title] [user_id]
# 示例:
#   ./ingest_repo.sh /c/Kais_Projects/Git_Projects/Github/starMindAgent "starMindAgent 架构"
#
# 前置:
#   1. sMA 后端已启动（默认 http://127.0.0.1:8000，可用 SMA_BACKEND 覆盖）
#   2. claude CLI 已安装且已登录
set -euo pipefail

REPO_PATH="${1:?用法: $0 <repo_path> [title] [user_id]}"
TITLE="${2:-$(basename "$REPO_PATH") 代码库学习材料}"
USER_ID="${3:-default}"
SMA_BACKEND="${SMA_BACKEND:-http://127.0.0.1:8000}"
OUT_FILE="$(dirname "$0")/ingest_$(basename "$REPO_PATH").md"

echo "[1/3] claude headless 消化代码库: $REPO_PATH"
claude -p "$(cat <<'PROMPT'
你正在为一个「概念探索学习系统」准备学习材料。请消化当前工作目录下的代码库，产出一份结构化 markdown 文档（中文），作为学习者理解这个代码库的核心材料。

要求：
1. 用 ## 二级标题组织以下板块（每个板块就是后续学习的一个锚点）：
   - 项目定位：这个项目解决什么问题、核心价值
   - 整体架构：主要模块、分层、数据流
   - 核心机制：挑 3-6 个最关键的机制/设计，各自单独一节，讲清楚「是什么、为什么这么设计、怎么运转」
   - 关键模块说明：每个主要模块一小节（职责、入口、与其他模块的交互）
   - 数据模型：核心实体与关系
   - 关键设计取舍：代码里体现出的 trade-off 及理由
2. 机制描述里出现的关键术语/概念名保持原样（代码标识符不翻译），因为它们会被下游系统抽取为「概念」。
3. 直接输出 markdown 正文，不要代码块包裹，不要开场白和总结语。
4. 篇幅以覆盖为优先，宁可每节精炼也不要遗漏模块。
PROMPT
)" --add-dir "$REPO_PATH" > "$OUT_FILE"

LINES=$(wc -l < "$OUT_FILE")
CHARS=$(wc -m < "$OUT_FILE")
echo "    产出: $OUT_FILE ($LINES 行 / $CHARS 字符)"
if [ "$CHARS" -lt 500 ]; then
  echo "错误: 消化产出过短（<500 字符），可能 claude CLI 未正确执行" >&2
  exit 1
fi

echo "[2/3] POST 到 sMA /learning/import"
python - "$SMA_BACKEND" "$USER_ID" "$TITLE" "$OUT_FILE" <<'PYEOF'
import json, sys, urllib.request

backend, user_id, title, path = sys.argv[1:5]
with open(path, encoding="utf-8") as f:
    content = f.read()
payload = json.dumps({"user_id": user_id, "title": title, "content": content}).encode()
req = urllib.request.Request(
    f"{backend}/learning/import", data=payload,
    headers={"Content-Type": "application/json"}, method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
except urllib.error.HTTPError as e:
    print(f"导入失败 HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
    sys.exit(1)
print("    material_id:", out.get("material_id"))
print("    qa_id(L0根):", out.get("qa_id"))
print("    concepts:", len(out.get("concepts", [])) if isinstance(out.get("concepts"), list) else out.get("concepts"))
PYEOF

echo "[3/3] 完成。打开前端选中该材料提问，或用 MCP server 的 ask 工具开始学习。"
