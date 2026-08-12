-- 学习记忆层（学习画像 + 推荐）
-- QASession.user_id 已在 001 定义但闲置，本迁移补 User 主表 + UserProfile 画像表
-- 画像由 LLM 周期性总结历史 QA 生成，存 JSONB，双轨：原始数据在 qa_session/qa_step，画像在此

CREATE TABLE IF NOT EXISTS app_user (
    user_id      TEXT        PRIMARY KEY,           -- 外部传入的 user_id（轻量，免登录）
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_profile (
    user_id           TEXT        PRIMARY KEY REFERENCES app_user(user_id) ON DELETE CASCADE,
    -- LLM 总结的画像 JSONB，结构: {mastered:[], weak:[], interests:[], recommendation:"", summary:""}
    profile           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    qa_count          INT         NOT NULL DEFAULT 0,    -- 总结时覆盖的 QA 数
    concept_count     INT         NOT NULL DEFAULT 0,     -- 涉及概念数
    last_summary_at   TIMESTAMPTZ,                        -- 上次总结时间（NULL=从未总结）
    summary_model     TEXT,                               -- 用的哪个模型总结的
    version           INT         NOT NULL DEFAULT 1      -- 乐观锁
);

CREATE INDEX IF NOT EXISTS idx_qa_session_user ON qa_session (user_id);
CREATE INDEX IF NOT EXISTS idx_qastep_updated ON qa_step (updated_at);
