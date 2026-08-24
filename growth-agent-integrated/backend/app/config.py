"""应用配置。

阈值与 golden 规则由「数据产品经理」提供，封装为独立服务 (concept.thresholds)，
便于灰度调参不动主链路。这里只持有数据源位置与全局开关。
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # —— 数据库 ——
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://dev:dev@localhost:5432/growth_agent"
    )

    # —— 推理框架 ——
    # 推理框架工程师提供「单次调用流式正文 + 尾部结构化 JSON」协议，
    # Sentinel 由双方约定，这里集中配置。
    concept_sentinel: str = os.getenv("CONCEPT_SENTINEL", "≡≡CONCEPT_BLOCK≡≡")
    inference_timeout_s: float = float(os.getenv("INFERENCE_TIMEOUT_S", "60"))
    first_token_timeout_s: float = float(os.getenv("FIRST_TOKEN_TIMEOUT_S", "5"))
    # 网关 glm-5.3 强制思考模式：非流式 complete_text（概念抽取兜底/层摘要）
    # 首个可见 token 前有长思考期，15s 会 ReadTimeout -> 层有正文但 0 概念
    json_parse_timeout_s: float = float(os.getenv("JSON_PARSE_TIMEOUT_S", "60"))

    # —— 膨胀控制（技术架构文档 第十一节风险表 / 开发要求）——
    max_explore_depth: int = int(os.getenv("MAX_EXPLORE_DEPTH", "6"))
    max_concepts_per_session: int = int(os.getenv("MAX_CONCEPTS_PER_SESSION", "200"))

    # —— 异步补标注 worker ——
    backfill_max_retry: int = int(os.getenv("BACKFILL_MAX_RETRY", "3"))

    # —— 阈值数据源（数据产品经理提供）——
    # 实际从数据库 / 配置中心读取；本地开发用 JSON 文件
    thresholds_path: str = os.getenv(
        "THRESHOLDS_PATH", os.path.join(os.path.dirname(__file__), "thresholds.local.json")
    )


settings = Settings()
