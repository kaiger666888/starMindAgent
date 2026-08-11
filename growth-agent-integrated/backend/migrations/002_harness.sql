-- Harness 层持久化表（技术架构文档第七节）
-- checkpoint 表：网络断开后跨进程续推；harness 自带，不依赖主仓 001 表
-- 注意：主仓 backfill_task 已由 001_init.sql 提供，harness 复用之（SqlTaskStore）

CREATE TABLE IF NOT EXISTS harness_checkpoint (
    qa_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id         UUID        NOT NULL,
    answer_checkpoint  TEXT        NOT NULL DEFAULT '',   -- 已产出正文
    sentinel_position  INT         NOT NULL DEFAULT -1,    -- sentinel 检测位置
    json_state         TEXT        NOT NULL DEFAULT 'accumulating'
                       CHECK (json_state IN ('accumulating','parsed','failed')),
    status             TEXT        NOT NULL DEFAULT 'streaming'
                       CHECK (status IN ('streaming','interrupted','completed')),
    concept_block_raw  TEXT,                               -- 已累积 JSON 文本
    last_event_id      INT         NOT NULL DEFAULT 0,     -- SSE 续推序号
    model              TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_harness_cp_session ON harness_checkpoint (session_id);

-- 熔断/可观测采样（可选；默认进程内采样，生产可落库聚合）
CREATE TABLE IF NOT EXISTS harness_recovery_sample (
    sample_id   BIGSERIAL PRIMARY KEY,
    qa_id       UUID,
    success     BOOLEAN NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_recovery_sample_time ON harness_recovery_sample (created_at);
