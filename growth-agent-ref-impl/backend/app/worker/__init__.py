"""补标注 worker 入口（实现见 app.inference.tasks）。"""
from app.inference.tasks import backfill_queue, backfill_processor
__all__ = ["backfill_queue", "backfill_processor"]
