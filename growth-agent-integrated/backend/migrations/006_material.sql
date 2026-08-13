-- 006: 学习材料导入支持（markdown 文件作探索根 + 上下文注入）
-- 导入 md 文件存为 learning_material，QAStep 关联 material_id，下钻/问答时注入文件上下文

CREATE TABLE IF NOT EXISTS learning_material (
    material_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT        NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    title           TEXT        NOT NULL,           -- 文件名或标题
    content         TEXT        NOT NULL,           -- md 全文
    content_plain   TEXT        NOT NULL,           -- 纯文本(去 md 标记，供概念抽取/上下文检索)
    size_bytes      INT         NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_material_user ON learning_material (user_id, created_at DESC);

-- QAStep 关联学习材料（根层=L0 文件，子层继承根层 material_id 用于上下文注入）
ALTER TABLE qa_step ADD COLUMN IF NOT EXISTS material_id UUID REFERENCES learning_material(material_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_qastep_material ON qa_step (material_id) WHERE material_id IS NOT NULL;
