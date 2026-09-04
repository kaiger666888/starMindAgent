-- 008: 记忆卡片（盲 check 复习）
-- 后台 worker 周期扫描用户 checked 的 QA 层，调 LLM 把问答总结成卡片（QA 快照），
-- 用户第二天在复习分页盲 check：理解(streak+1，连续3天归档) / 忘记(重置，次日再到期)。
-- 卡片只在复习会话内更新 streak/due，不在复习中编辑内容。

CREATE TABLE IF NOT EXISTS memory_card (
    card_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            TEXT        NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    concept_id         UUID        REFERENCES concept_node(concept_id) ON DELETE CASCADE,  -- 卡片对应概念（可空：无概念 QA 也生成卡）
    qa_id              UUID        REFERENCES qa_step(qa_id) ON DELETE SET NULL,           -- 来源 QA 层（删足迹不删卡）
    session_id         UUID,                                                                 -- 来源会话（无 FK，与 concept_edge 同约定）
    concept_name       TEXT        NOT NULL,                -- 冗余概念名（概念节点可能被删/合并）
    question           TEXT        NOT NULL,                -- 卡片正面（LLM 总结的测试性问题或原问题）
    answer             TEXT        NOT NULL,                -- 卡片背面（LLM 总结的要点式答案）
    source_answer      TEXT,                                -- 原始回答快照（印证时展开看）
    status             TEXT        NOT NULL DEFAULT 'active',  -- active / archived
    streak             INT         NOT NULL DEFAULT 0,      -- 连续「理解」天数（≥3 归档）
    review_count       INT         NOT NULL DEFAULT 0,      -- 累计复习次数
    due_at             TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 下次到期时间（次日可复习）
    last_reviewed_at   TIMESTAMPTZ,                         -- 上次复习时间（NULL=从未复习）
    last_grade         TEXT,                                -- understood / forgot / retry（用户上次自评）
    generator_model    TEXT,                                -- 生成卡片的模型（画像同款约定）
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_card_status CHECK (status IN ('active','archived')),
    CONSTRAINT ck_card_grade CHECK (last_grade IN ('understood','forgot','retry') OR last_grade IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_card_user_due ON memory_card (user_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_card_user_created ON memory_card (user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_card_user_qa_concept ON memory_card (user_id, qa_id, concept_id);
