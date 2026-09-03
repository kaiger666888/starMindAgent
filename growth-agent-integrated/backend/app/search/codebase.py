"""代码库 grounding Provider（本地 ripgrep，零依赖）。

用途：QA 循环的 code_query 工具——模型对「这个代码库里」的问题调用它，
拿回真实代码片段拼进第二轮 prompt（与 web_search 的 prompt 注入路径同构，
不走 role=tool 回填，原因见 backend.py stream() 注释）。

设计对齐 app/search/provider.py：
- run_code_search(queries) 对应 run_searches(queries)
- 未配置 CODEBASE_DIR 时返回空（调用方跳过，链路零影响）
- 单 query 失败不阻断（异常吞掉记日志）

环境变量：
  CODEBASE_DIR   要 grounding 的仓库根目录（绝对路径）；未设 = 功能关闭
  CODEBASE_MAX_HITS 每个 query 最多取的文件数（默认 4）
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class CodeHit:
    """单条代码命中：文件路径 + 匹配行（含上下文）。"""
    path: str
    line_no: int
    text: str


def _rg_bin() -> str:
    """ripgrep 可执行文件：PATH 里的 rg，找不到返回空串。"""
    import shutil
    return shutil.which("rg") or ""


def codebase_root() -> str | None:
    """返回已配置的代码库根目录；未配置/不存在返回 None。"""
    root = os.getenv("CODEBASE_DIR", "").strip()
    if not root:
        return None
    if not os.path.isdir(root):
        log.warning("CODEBASE_DIR %s is not a dir, code grounding disabled", root)
        return None
    return root


async def run_code_search(queries: list[str], max_queries: int = 2) -> tuple[str, list[CodeHit]]:
    """并行 rg 多 query，返回 (合并 prompt 文本, 命中列表)。

    每个 query 取前 N 个文件的匹配行（含行号），prompt 文本按 query 分组编号
    （与 run_searches 的 prompt 结构对齐，拼 user prompt 用）。
    max_queries 硬上限 2（代码查询比联网搜索更贵于上下文，防放大）。
    """
    root = codebase_root()
    rg = _rg_bin()
    if not root or not rg or not queries:
        return "", []
    queries = [q.strip() for q in queries if q and q.strip()][:max_queries]
    if not queries:
        return "", []
    max_hits = int(os.getenv("CODEBASE_MAX_HITS", "4"))
    results = await asyncio.gather(
        *(_rg_one(rg, root, q, max_hits) for q in queries),
        return_exceptions=True,
    )
    prompt_parts: list[str] = []
    hits: list[CodeHit] = []
    for q, r in zip(queries, results):
        if isinstance(r, Exception) or not r:
            continue
        found = r
        if not found:
            continue
        lines = [f"- {h.path}:{h.line_no}: {h.text}" for h in found]
        prompt_parts.append(f"代码库中与「{q}」相关的片段：\n" + "\n".join(lines))
        hits.extend(found)
    return "\n\n".join(prompt_parts), hits


async def _rg_one(rg: str, root: str, query: str, max_hits: int) -> list[CodeHit]:
    """单 query rg 执行：-i 智能大小写、-n 行号、--max-count 1 每文件只取首匹配。

    max-count=1 拿「文件级」命中分布（比单文件多行更利于模型定位模块），
    -A 8 后置上下文覆盖 docstring/函数签名（-A 2 实测只见声明行，
    模型拿不到机制描述）。超时 10s 兜底。
    rg 输出格式（--no-heading）: `path:line_no:text`；上下文行是 `path-line-text`。
    """
    proc = await asyncio.create_subprocess_exec(
        rg, "-i", "-n", "--no-heading", "--max-count", "1", "-A", "8",
        "--glob", "!*.{lock,min,map,svg,png,jpg}",
        "-e", query, root,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        cwd=root,  # 相对路径输出，prompt 里更短
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except asyncio.TimeoutError:
        proc.kill()
        return []
    text = out.decode("utf-8", errors="ignore").strip()
    if not text:
        return []
    # rg 对绝对路径参数会原样输出绝对路径（cwd 不影响）；剥掉 root 前缀
    # 让 prompt 里的路径短（模型定位模块足够，完整路径在 root 已知）
    prefix = root.rstrip("/\\") + os.sep
    hits: list[CodeHit] = []
    seen_files: set[str] = set()
    for line in text.splitlines():
        if line.startswith("--"):  # rg 的 match 分隔行
            continue
        if line.startswith(prefix):
            line = line[len(prefix):]
        m = _split_hit(line)
        if m is None:
            continue
        path, no, rest = m
        if path in seen_files:
            continue
        seen_files.add(path)
        hits.append(CodeHit(path=path, line_no=no, text=rest[:300]))
        if len(hits) >= max_hits:
            break
    return hits


def _split_hit(line: str) -> tuple[str, int, str] | None:
    """拆 rg 单行输出：匹配行 'path:line:text' / 上下文行 'path-line-text'。

    文本部分可能含 ':' '-' 和数字（如注释里的「2026:8: 31」），不能盲拆。
    判据：path 段必须是纯 ASCII 路径字符（相对路径无盘符、无中文空格），
    行号段纯数字。从左往右枚举分隔符位置（path 从短到长），第一个
    满足两条件的组合即真实拆分——行首必是路径，最短的合法 path 就是它。
    """
    import re
    for j, ch in enumerate(line):
        if ch not in (":", "-") or j == 0:
            continue
        # 找 j 右侧最近的分隔符 i，使 line[j+1:i] 恰为行号
        for i in range(j + 2, len(line)):
            if line[i] in (":", "-"):
                mid = line[j + 1:i]
                if mid.isdigit() and re.fullmatch(r"[A-Za-z0-9_.\-/\\]+", line[:j]):
                    return line[:j], int(mid), line[i + 1:]
                break  # j 后第一个分隔段不是数字，这个 j 不成立
    return None
