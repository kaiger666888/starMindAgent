-- 007: QAStep 学习完成度手动勾选（左边栏 check 框）
-- checked = 学习者手动标记该层已读完/已掌握；前端据此渲染深绿背景 + 进度条。

ALTER TABLE qa_step ADD COLUMN IF NOT EXISTS checked BOOLEAN NOT NULL DEFAULT false;
