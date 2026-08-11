"""上下文膨胀控制（协议设计文档第六节）。

深层 prompt 只保留「当前问题 + 上层概念链摘要」，每层 ≤ 2K token。
预算：system(~400) + 概念链摘要(~1000) + 当前问题(~400) + 余量(~200)。
超出预算时从最上层逐层压缩，压成 canonical_name + 一句话定位；
仍超限则丢弃非当前分支的兄弟概念，只保留当前下钻路径主干。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# token 预算（协议 6.1）
SYSTEM_BUDGET = 400
CHAIN_BUDGET = 1000
QUESTION_BUDGET = 400
MARGIN_BUDGET = 200
TOTAL_BUDGET = SYSTEM_BUDGET + CHAIN_BUDGET + QUESTION_BUDGET + MARGIN_BUDGET  # 2000

# 单树最大 6 层、单会话概念上限 200（协议 6.2 / PRD）
MAX_EXPLORE_DEPTH = 6
MAX_CONCEPTS_PER_SESSION = 200


@dataclass
class LayerSummary:
    """一层下钻的摘要：canonical_name + 一句话定位 + 该层抽取的全部概念。"""

    canonical_name: str
    one_liner: str
    sibling_concepts: list[str] = field(default_factory=list)
    is_current_branch: bool = True


def estimate_tokens(text: str) -> int:
    """粗估 token 数：优先 tiktoken，不可用则按字符数 * 0.6（中文偏保守）。"""
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # 中文 ~1.5 字/token，英文 ~4 字符/token，取 0.6 折中
        return max(1, int(len(text) * 0.6))


@dataclass
class ContextBudget:
    """构造深层 prompt 并控制膨胀。

    用法：
        cb = ContextBudget()
        messages = cb.build_messages(
            question="什么是反向传播",
            parent_chain=["神经网络", "梯度下降"],
            layer_summaries=[...],
        )
    """

    total_budget: int = TOTAL_BUDGET
    system_budget: int = SYSTEM_BUDGET
    chain_budget: int = CHAIN_BUDGET
    question_budget: int = QUESTION_BUDGET

    def build_messages(
        self,
        question: str,
        parent_chain: Optional[list[str]] = None,
        layer_summaries: Optional[list[LayerSummary]] = None,
        system_prompt: Optional[str] = None,
    ) -> list[dict]:
        """构造 chat messages，保证总 token ≤ total_budget。"""
        sys_text = system_prompt or DEFAULT_SYSTEM_PROMPT
        sys_text = self._fit(sys_text, self.system_budget)

        question_text = self._fit(question, self.question_budget)

        chain_text = self._build_chain_summary(layer_summaries or [], parent_chain or [])

        messages = [
            {"role": "system", "content": sys_text},
            {"role": "user", "content": f"{chain_text}\n\n问题：{question_text}".strip()},
        ]
        return messages

    def build_extract_messages(
        self, answer_text: str, question: str, parent_chain: Optional[list[str]] = None
    ) -> list[dict]:
        """构造「仅结构化部分」的抽取调用 messages（协议 4.2 重试 / L2 第二次调用）。

        输入 = 已产出正文 + 原始问题，要求模型只输出 ConceptBlock JSON，不重新生成正文。
        """
        sys_text = self._fit(EXTRACT_SYSTEM_PROMPT, self.system_budget)
        chain_hint = "、".join(parent_chain[-3:]) if parent_chain else ""
        user = (
            f"问题：{self._fit(question, 200)}\n"
            f"上层概念链：{chain_hint}\n"
            f"回答正文：\n{self._fit(answer_text, 1200)}\n"
            f"\n请仅输出符合 ConceptBlock schema 的 JSON，不要输出正文、不要 markdown 围栏。"
        )
        return [
            {"role": "system", "content": sys_text},
            {"role": "user", "content": user},
        ]

    # ----- internals -----
    def _build_chain_summary(
        self, layer_summaries: list[LayerSummary], parent_chain: list[str]
    ) -> str:
        """构造上层概念链摘要，超限时逐层压缩 / 丢弃非当前分支兄弟。"""
        if not layer_summaries and not parent_chain:
            return ""

        # 第一遍：每层压成「canonical_name — one_liner」
        lines: list[str] = []
        for ls in layer_summaries:
            lines.append(f"- {ls.canonical_name}：{ls.one_liner}")

        # 若仅 parent_chain（无详细摘要），用 canonical_name 列表
        if not lines and parent_chain:
            lines = [f"- {name}" for name in parent_chain]

        text = "上层概念链摘要：\n" + "\n".join(lines)
        if estimate_tokens(text) <= self.chain_budget:
            return text

        # 第二遍压缩：去掉 one_liner，只留 canonical_name
        lines = [f"- {ls.canonical_name}" for ls in layer_summaries] or [
            f"- {name}" for name in parent_chain
        ]
        text = "上层概念链：\n" + "\n".join(lines)
        if estimate_tokens(text) <= self.chain_budget:
            return text

        # 第三遍：丢弃非当前分支的兄弟概念，只保留当前下钻路径主干
        main = [ls for ls in layer_summaries if ls.is_current_branch] or layer_summaries
        lines = [f"- {ls.canonical_name}" for ls in main]
        if not lines and parent_chain:
            lines = [f"- {name}" for name in parent_chain]
        text = "上层概念链（仅主干）：\n" + "\n".join(lines)
        return self._fit(text, self.chain_budget)

    def _fit(self, text: str, budget: int) -> str:
        """若 text 超预算，按字符截断并标注截断。"""
        if estimate_tokens(text) <= budget:
            return text
        # 粗略反推字符上限（token * 1.6 ≈ 字符）
        char_cap = max(1, int(budget / 0.6))
        if len(text) <= char_cap:
            return text
        return text[:char_cap].rsplit(" ", 1)[0] + "…[已压缩]"


# ---------------------------------------------------------------------------
# Prompt 模板（协议 2.4）
# ---------------------------------------------------------------------------
DEFAULT_SYSTEM_PROMPT = """你是一个学习辅导助手。请按以下协议输出：

1. 先输出自然语言正文回答用户问题；
2. 正文结束后，另起一行独占一行输出标记：≡≡CONCEPT_BLOCK≡≡
3. 标记之后再输出一个 JSON 对象，符合 ConceptBlock schema：
   {"concepts":[{"name":str,"aliases":[str],"confidence":float,"relation_type":str}],"model":str}

要求：
- 正文中禁止出现 ≡≡CONCEPT_BLOCK≡≡ 标记，该标记仅用于分隔正文与结构化输出；
- 整段输出中该标记只出现一次，位于正文与 JSON 之间；
- JSON 段为合法 JSON，无 markdown 代码围栏、无前后多余文本；
- 正文为自然语言，不含 JSON 片段。
"""

EXTRACT_SYSTEM_PROMPT = """你是一个概念抽取器。从给定的回答正文中抽取关键概念，
只输出符合 ConceptBlock schema 的 JSON：
{"concepts":[{"name":str,"aliases":[str],"confidence":float,"relation_type":str}],"model":str}
不要输出正文、不要 markdown 围栏、不要解释。"""
