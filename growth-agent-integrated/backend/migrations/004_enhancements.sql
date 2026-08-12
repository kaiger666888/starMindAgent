-- 004: 学习 Agent 需求增强字段（层摘要 + 概念成熟度 + 红色语义双计数 + 放弃探索标记）
-- 对照《「伴你成长」需求描述》二/三/四/六节

-- 层摘要：QAStep 在概念列表之上生成"这层你理解了什么"，作树节点折叠预览
ALTER TABLE qa_step ADD COLUMN IF NOT EXISTS layer_summary TEXT;

-- 放弃探索标记：用户看完答案未下钻
ALTER TABLE qa_step ADD COLUMN IF NOT EXISTS browsed_not_drilled BOOLEAN NOT NULL DEFAULT false;

-- 上次看的概念：回上层时高亮"你上次在这里看的概念"
ALTER TABLE qa_step ADD COLUMN IF NOT EXISTS last_viewed_concept_id UUID REFERENCES concept_node(concept_id) ON DELETE SET NULL;

-- 概念节点双计数：区分"反复下钻"(主动探索，暗示复杂需深入) vs "反复回访"(回到已探索，暗示重要需巩固)
-- drill_down_count: 被点击下钻次数（主动探索）
-- visit_count: 被回访次数（回上层后再看到）
-- explore_count 保留为总探索次数（= drill_down + visit），跨 session 累计
ALTER TABLE concept_node ADD COLUMN IF NOT EXISTS drill_down_count INT NOT NULL DEFAULT 0;
ALTER TABLE concept_node ADD COLUMN IF NOT EXISTS visit_count INT NOT NULL DEFAULT 0;

-- 概念成熟度：explore≥2 且未下钻就回上层 → understood
-- 用 ConceptNode 字段标记（全局），而非 QAStep 级
ALTER TABLE concept_node ADD COLUMN IF NOT EXISTS understood BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE concept_node ADD COLUMN IF NOT EXISTS last_explored_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_concept_understood ON concept_node (understood) WHERE understood = true;
