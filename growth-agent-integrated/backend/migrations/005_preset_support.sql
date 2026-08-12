-- 005: 预置概念种子支持（需求五"预置+LLM补充"）
-- concept_node.canonical_name 加唯一约束，使 seed 可幂等 ON CONFLICT DO NOTHING

-- 先加普通列唯一约束（ON CONFLICT (canonical_name) 需要列约束，函数索引不行）
CREATE UNIQUE INDEX IF NOT EXISTS uq_concept_canonical_name
  ON concept_node (canonical_name);

-- 大小写不敏感的唯一索引（防止 "CNN" 与 "cnn" 重复）
CREATE UNIQUE INDEX IF NOT EXISTS uq_concept_canonical_name_lower
  ON concept_node (lower(canonical_name));
