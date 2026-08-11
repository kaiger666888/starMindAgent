"""归一化阈值与 golden 规则服务（独立服务，便于灰度调参不动主链路）。

阈值与 golden clusters 由「数据产品经理」提供。技术架构文档第十节：
golden concept clusters 状态为「待提供」，不阻塞主链路——本服务在缺数据时
使用安全默认阈值，数据到位后热加载即可。
"""
from __future__ import annotations
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Thresholds:
    # 归一化三级流水线阈值
    alias_exact_match: bool = True            # 第一级：别名精确匹配
    embedding_recall_topk: int = 5           # 第二级：embedding 召回 top-k
    embedding_high: float = 0.92              # >= 阈值直接合并
    embedding_low: float = 0.78               # < 阈值直接保留（不合并）
    # 灰区 [embedding_low, embedding_high) 进 LLM 二次判定
    llm_gray_zone_enabled: bool = True

    # 评测门禁（技术架构文档 9.2）：误合并率 < 5%，合并 Recall > 85%
    # 这里用更保守的运行时阈值以保门禁
    target_merge_precision: float = 0.95
    target_merge_recall: float = 0.85


_DEFAULT = Thresholds()


class ThresholdService:
    """独立阈值服务：从 JSON / 配置中心热加载，主链路只读不写。"""

    def __init__(self, path: str | None = None):
        self.path = path
        self._t = _DEFAULT
        self._mtime = 0.0
        if path and os.path.exists(path):
            self._reload()

    def _reload(self) -> None:
        try:
            p = Path(self.path)
            mtime = p.stat().st_mtime
            if mtime == self._mtime:
                return
            data = json.loads(p.read_text("utf-8"))
            self._t = Thresholds(**data)
            self._mtime = mtime
            log.info("thresholds reloaded from %s", self.path)
        except Exception as e:  # 配置异常时回退默认，不阻塞主链路
            log.warning("thresholds load failed (%s), fallback to defaults", e)
            self._t = _DEFAULT

    def get(self) -> Thresholds:
        """每次读取前尝试热加载（文件 mtime 变更才真正重读）。"""
        if self.path and os.path.exists(self.path):
            self._reload()
        return self._t


# 全局单例
threshold_service = ThresholdService(os.getenv("THRESHOLDS_PATH"))
