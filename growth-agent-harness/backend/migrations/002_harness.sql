-- 「伴你成长」Harness 生命周期层 — 持久化表
-- 对应设计规格 §三（checkpoint 恢复）/ §五（异步补标注持久任务）。
-- 主仓 001_init.sql 已建 qa_step.answer_offset（harness checkpoint 基准）；
-- 本迁移补 Harness 自有的 checkpoint 快照表与持久任务表。

-- ---------------------------------------------------------------------------
-- harness_checkpoint：每个 QAStep 的流式中断恢复现场
-- 恢复协议以 qa_id + offset 为准（协议 §7.2）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness_checkpoint (
    qa_id              UUID        PRIMARY KEY,
    session_id         UUID        NOT NULL,
    answer_checkpoint  TEXT        NOT NULL DEFAULT '',   -- 已产出正文（不含 sentinel/JSON）
    sentinel_position  INT         NOT NULL DEFAULT -1,   -- sentinel 检测位置，-1 未遇到
    json_state         TEXT        NOT NULL DEFAULT 'idle',  -- idle/accumulating/parsed/failed
    status             TEXT        NOT NULL DEFAULT 'streaming',  -- streaming/interrupted/completed
    degrade_level      TEXT        NOT NULL DEFAULT 'L0',  -- L0/L1/L2/L3
    "offset"           INT         NOT NULL DEFAULT 0,     -- len(answer_checkpoint)，恢复基准
    last_event_id      INT         NOT NULL DEFAULT 0,     -- SSE last-event-id 续推基准
    concept_ids        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    call_id            TEXT,
    endpoint           TEXT,
    raw_json           TEXT        NOT NULL DEFAULT '',    -- L1 已累积 JSON 片段，供异步补标注复用
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_harness_jsonstate CHECK (json_state IN ('idle','accumulating','parsed','failed')),
    CONSTRAINT ck_harness_session_status CHECK (status IN ('streaming','interrupted','completed')),
    CONSTRAINT ck_harness_degrade CHECK (degrade_level IN ('L0','L1','L2','L3'))
);
CREATE INDEX IF NOT EXISTS idx_harness_cp_session ON harness_checkpoint (session_id);

-- ---------------------------------------------------------------------------
-- harness_task：异步补标注 / 归一化持久任务（worker 池消费，不进内存队列）
-- 状态含 reclaimed（孤儿回收），是对主仓 backfill_task 的生产级扩展
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness_task (
    task_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    qa_id        UUID,                               -- reannotation 关联；归一化可为空
    session_id   UUID,
    kind         TEXT        NOT NULL DEFAULT 'reannotation',  -- reannotation/normalization
    status       TEXT        NOT NULL DEFAULT 'pending',       -- pending/running/done/dead/reclaimed
    retry_count  INT         NOT NULL DEFAULT 0,
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,     -- reannotation:{answer_snapshot,reason}; normalization:{action,...}
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_harness_task_kind CHECK (kind IN ('reannotation','normalization')),
    CONSTRAINT ck_harness_task_status CHECK (status IN ('pending','running','done','dead','reclaimed'))
);
-- worker claim：FOR UPDATE SKIP LOCKED 走该部分索引
CREATE INDEX IF NOT EXISTS idx_harness_task_pending
    ON harness_task (created_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_harness_task_status ON harness_task (status);

-- updated_at 自动维护
CREATE TRIGGER trg_harness_task_touch BEFORE UPDATE ON harness_task
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_harness_cp_touch BEFORE UPDATE ON harness_checkpoint
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
