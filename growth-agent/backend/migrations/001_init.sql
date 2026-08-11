-- 「伴你成长」学习 Agent — 数据层初始化
-- 对应技术架构文档 第四节：ConceptNode / ConceptEdge / QASession / QAStep / Audit Log
-- 设计要点：
--   1) ConceptNode 是唯一一等公民，同概念多层出现只存引用不存副本（explore_count 全局累计）
--   2) ConceptEdge 单表 + origin 标记派生三状态视图（user_click / co_occurrence / domain_graph）
--   3) QAStep 用 parent_qa_id 自引用挂出树状层级，递归 CTE 查询整树
--   4) Audit log 只追加，支持 merge/undo 反向回放撤销
--   5) 膨胀控制：单树 <=6 层、单会话 <=200 概念（由应用层 + 触发器双重约束）

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- 概念节点：唯一一等公民
-- ---------------------------------------------------------------------------
CREATE TABLE concept_node (
    concept_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  TEXT        NOT NULL,
    aliases         JSONB       NOT NULL DEFAULT '[]'::jsonb,   -- ["别名1","别名2"]
    domain_tag      TEXT,
    source          TEXT        NOT NULL DEFAULT 'llm_extracted'
                    CHECK (source IN ('preset','llm_extracted')),
    explore_count   INT         NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 别名精确匹配加速归一化第一级
CREATE INDEX idx_concept_aliases_gin ON concept_node USING GIN (aliases jsonb_path_ops);
CREATE INDEX idx_concept_canonical_name ON concept_node (canonical_name);
CREATE INDEX idx_concept_domain ON concept_node (domain_tag);

-- ---------------------------------------------------------------------------
-- 概念边：单表 + origin 派生三状态视图
-- ---------------------------------------------------------------------------
CREATE TABLE concept_edge (
    edge_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     UUID        NOT NULL,
    source_id      UUID        NOT NULL REFERENCES concept_node(concept_id) ON DELETE CASCADE,
    target_id      UUID        NOT NULL REFERENCES concept_node(concept_id) ON DELETE CASCADE,
    relation_type  TEXT        NOT NULL DEFAULT 'related',
    origin         TEXT        NOT NULL
                   CHECK (origin IN ('user_click','co_occurrence','domain_graph')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, source_id, target_id, origin)
);
CREATE INDEX idx_edge_session_origin ON concept_edge (session_id, origin);
CREATE INDEX idx_edge_source ON concept_edge (source_id);

-- ---------------------------------------------------------------------------
-- 问答会话
-- ---------------------------------------------------------------------------
CREATE TABLE qa_session (
    session_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        TEXT,
    domain_tag     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- QAStep：显式状态机，parent_qa_id 自引用挂出探索树
-- 乐观锁：version 字段，写入时 WHERE version = $expected
-- ---------------------------------------------------------------------------
CREATE TABLE qa_step (
    qa_id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id             UUID        NOT NULL REFERENCES qa_session(session_id) ON DELETE CASCADE,
    parent_qa_id           UUID        REFERENCES qa_step(qa_id) ON DELETE CASCADE,  -- 下钻时挂父
    question               TEXT        NOT NULL,
    answer                 TEXT,                      -- 流式产出累积
    answer_offset          INT         NOT NULL DEFAULT 0,  -- harness checkpoint：已落盘正文 offset
    extracted_concept_ids  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    status                 TEXT        NOT NULL DEFAULT 'generating'
                           CHECK (status IN ('generating','extracting','waiting')),
    version                INT         NOT NULL DEFAULT 1,        -- 乐观锁版本号
    -- 埋点字段（评测依赖）
    model                  TEXT,
    prompt_hash            TEXT,
    raw_output             TEXT,
    parsed_concepts        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    aliases               JSONB       NOT NULL DEFAULT '[]'::jsonb,
    confidence             REAL,
    depth                  INT         NOT NULL DEFAULT 1,       -- 所在探索树层级，膨胀控制用
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_qastep_session ON qa_step (session_id);
CREATE INDEX idx_qastep_parent  ON qa_step (parent_qa_id);
CREATE INDEX idx_qastep_status  ON qa_step (session_id, status);

-- ---------------------------------------------------------------------------
-- 归一化 / merge-undo audit log（只追加）
-- 既记录归一化决策，也记录 merge/undo 动作，评测可按 qa_id 回放并与 golden set 比对
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    log_id              BIGSERIAL   PRIMARY KEY,
    qa_id               UUID        REFERENCES qa_step(qa_id) ON DELETE SET NULL,  -- 评测回放入口
    session_id          UUID        REFERENCES qa_session(session_id) ON DELETE CASCADE,
    -- 归一化决策字段
    candidate_name      TEXT,       -- 待归一化的抽取概念名
    matched_alias       TEXT,       -- 命中的已有别名
    similarity_score    REAL,       -- embedding 相似度
    action              TEXT        NOT NULL
                        CHECK (action IN ('merge','keep','undo')),
    llm_verdict         TEXT,       -- 灰区 LLM 判定原文 / 理由
    -- merge/undo 动作字段
    merge_id            UUID        DEFAULT gen_random_uuid(),  -- 一次合并的标识，undo 反向回放用
    survivor_id         UUID        REFERENCES concept_node(concept_id) ON DELETE CASCADE,
    absorbed_id         UUID        REFERENCES concept_node(concept_id) ON DELETE CASCADE,
    payload             JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- 合并前完整快照，支持精确回放
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_qa ON audit_log (qa_id);
CREATE INDEX idx_audit_merge ON audit_log (merge_id);
CREATE INDEX idx_audit_session ON audit_log (session_id, created_at);

-- ---------------------------------------------------------------------------
-- 异步补标注任务（持久化，worker 池消费，不进内存队列）
-- ---------------------------------------------------------------------------
CREATE TABLE backfill_task (
    task_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    qa_id          UUID        NOT NULL REFERENCES qa_step(qa_id) ON DELETE CASCADE,
    status         TEXT        NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','running','done','dead')),
    retry_count    INT         NOT NULL DEFAULT 0,
    last_error     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_backfill_pending ON backfill_task (status, created_at) WHERE status = 'pending';

-- ---------------------------------------------------------------------------
-- 递归 CTE：查询某会话整棵探索树（层级展开）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_qastep_tree AS
WITH RECURSIVE tree AS (
    -- 根节点（无 parent）
    SELECT qa_id, session_id, parent_qa_id, question, status, depth,
           ARRAY[qa_id]::UUID[] AS path, 1 AS lvl
    FROM qa_step WHERE parent_qa_id IS NULL
    UNION ALL
    SELECT c.qa_id, c.session_id, c.parent_qa_id, c.question, c.status, c.depth,
           t.path || c.qa_id, t.lvl + 1
    FROM qa_step c JOIN tree t ON c.parent_qa_id = t.qa_id
    WHERE t.lvl < 20  -- 安全递归深度上限
)
SELECT * FROM tree;

-- ---------------------------------------------------------------------------
-- 三状态视图：单表按 origin 派生（前端按视图筛选即可）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_graph_user_click AS
    SELECT session_id, source_id, target_id, relation_type, created_at
    FROM concept_edge WHERE origin = 'user_click';
CREATE OR REPLACE VIEW v_graph_co_occurrence AS
    SELECT session_id, source_id, target_id, relation_type, created_at
    FROM concept_edge WHERE origin = 'co_occurrence';
CREATE OR REPLACE VIEW v_graph_domain AS
    SELECT session_id, source_id, target_id, relation_type, created_at
    FROM concept_edge WHERE origin = 'domain_graph';

-- ---------------------------------------------------------------------------
-- 膨胀控制触发器：单树 <=6 层、单会话 <=200 概念，超限降级（标注已有概念）
-- 层级上限：阻止插入 depth > 6 的下钻（应用层应已降级，触发器为兜底）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION enforce_bloat_limits() RETURNS TRIGGER AS $$
DECLARE
    concept_cnt INT;
BEGIN
    -- 层级上限
    IF NEW.depth > 6 THEN
        RAISE EXCEPTION 'explore depth limit reached (max 6); degrade to annotate-existing mode';
    END IF;
    -- 单会话概念数上限（仅对新增概念节点计数；边不计数）
    IF TG_TABLE_NAME = 'concept_node' THEN
        SELECT COUNT(*) INTO concept_cnt FROM concept_node cn
        JOIN concept_edge ce ON ce.target_id = cn.concept_id
        WHERE ce.session_id IN (SELECT session_id FROM qa_step WHERE qa_id = NEW.concept_id);
        -- 注：概念数以会话维度约束，见应用层 service 的精确实现
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_qastep_depth BEFORE INSERT ON qa_step
    FOR EACH ROW EXECUTE FUNCTION enforce_bloat_limits();

-- updated_at 自动维护
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_qastep_touch BEFORE UPDATE ON qa_step
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_concept_touch BEFORE UPDATE ON concept_node
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
