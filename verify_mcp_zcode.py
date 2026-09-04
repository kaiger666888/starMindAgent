"""verify_mcp_zcode.py — starmind MCP server 在 ZCode 配置下的全链路验证。

模拟 ZCode 的接入方式：读仓库根 .zcode/config.json 的 starmind 条目，
按其 command/args/env 拉起 stdio 子进程，走完整 MCP 生命周期：
  1. initialize 握手（protocolVersion 协商）
  2. tools/list（8 个工具齐全）
  3. tools/call learning_materials（后端联通性，无副作用）
  4. tools/call ask（SSE 全链路：后端 -> LLM 网关 -> 概念抽取）

用法: python verify_mcp_zcode.py  （后端需已在 :8000 运行）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))

# 依赖 ZCode 实际会用的 python 环境（config.json 里的绝对路径）同源 SDK
sys.path.insert(0, os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python312",
    "Lib", "site-packages"))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def main() -> int:
    # 1) 读取 ZCode 工作区配置，完全按 ZCode 的拉起方式启动 server
    with open(os.path.join(REPO, ".zcode", "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    server = cfg["mcp"]["servers"]["starmind"]
    print(f"[config] command={server['command']}")
    print(f"[config] args={server['args']}")
    print(f"[config] env={server['env']} timeoutMs={server.get('timeoutMs')}")

    params = StdioServerParameters(
        command=server["command"],
        args=server["args"],
        env={**os.environ, **server["env"]},
    )

    failures: list[str] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # ---- 2) initialize 握手 ----
            init = await session.initialize()
            print(f"[initialize] server={init.server_info.name} "
                  f"v{init.server_info.version} protocol={init.protocol_version}")
            if init.server_info.name != "starmind-agent":
                failures.append(f"server name mismatch: {init.server_info.name}")

            # ---- 3) tools/list ----
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"[tools/list] {len(names)} tools: {', '.join(names)}")
            expected = {"import_material", "ask", "drilldown", "learning_materials",
                        "concept_graph", "session_tree", "learning_profile",
                        "list_sessions"}
            missing = expected - set(names)
            if missing:
                failures.append(f"missing tools: {missing}")

            # ---- 4) tools/call learning_materials（只读，验证后端联通）----
            r1 = await session.call_tool("learning_materials", {})
            text1 = r1.content[0].text if r1.content else ""
            print(f"[call learning_materials] isError={r1.is_error} "
                  f"resp={text1[:200]}")
            if r1.is_error:
                failures.append(f"learning_materials failed: {text1[:200]}")

            # ---- 5) tools/call ask（SSE 全链路，真实 LLM）----
            r2 = await session.call_tool("ask", {
                "question": "什么是 ripgrep？一句话回答。",
            })
            text2 = r2.content[0].text if r2.content else ""
            try:
                payload = json.loads(text2)
            except json.JSONDecodeError:
                failures.append(f"ask 返回非 JSON: {text2[:200]}")
                payload = {}
            print(f"[call ask] isError={r2.is_error} qa_id={payload.get('qa_id')}")
            print(f"[call ask] answer={payload.get('answer', '')[:150]}")
            print(f"[call ask] concepts={payload.get('concept_names', [])[:10]}")
            if r2.is_error or payload.get("error"):
                failures.append(f"ask failed: {payload.get('error') or text2[:200]}")
            elif not payload.get("answer"):
                failures.append("ask 返回空 answer")

    if failures:
        print("\nFAIL:")
        for f_ in failures:
            print(f"  - {f_}")
        return 1
    print("\nPASS: initialize + tools/list + learning_materials + ask 全链路通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
