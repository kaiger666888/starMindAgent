// 后端 API 客户端：QAStep 状态机三个出口 + concept 服务。
// 严格对应后端路由 app/api/routes_*.py

const BASE = ''  // 通过 vite proxy 转发到 :8000

export async function startQA(question, sessionId, domainTag, userId) {
  const res = await fetch(`${BASE}/qa/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sessionId, domain_tag: domainTag, user_id: userId }),
  })
  if (!res.ok) throw new Error(`start failed: ${res.status}`)
  return res.json()
}

// SSE 流式订阅 answer_delta / status / concepts / done
export function subscribeStream(qaId, handlers) {
  const es = new EventSource(`${BASE}/qa/${qaId}/stream`)
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data)
    const h = handlers[ev.type]
    if (h) h(ev, es)
    if (ev.type === 'done' || ev.type === 'error') es.close()
  }
  es.onerror = () => {
    // 网络断开：harness 凭 last-event-id 重连，前端丢弃本地未确认内容
    if (handlers._onerror) handlers._onerror(es)
  }
  return es
}

// 出口1：点击概念下钻（mode="ask" = 针对性提问，问题原样作子层，不走概念包装）
export async function drillDown(parentQaId, conceptId, question, mode) {
  const res = await fetch(`${BASE}/qa/${parentQaId}/drilldown`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ concept_id: conceptId, question, mode }),
  })
  if (!res.ok) throw new Error(`drilldown failed: ${res.status}`)
  return res.json()
}

// 出口2：回上层（栈式回退，状态保留）
export async function rollback(targetQaId) {
  const res = await fetch(`${BASE}/qa/${targetQaId}/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_qa_id: targetQaId }),
  })
  if (!res.ok) throw new Error(`rollback failed: ${res.status}`)
  return res.json()
}

// concept 服务
export async function getGraph(sessionId, originFilter) {
  const res = await fetch(`${BASE}/concept/graph`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, origin_filter: originFilter }),
  })
  return res.json()
}

export async function incrementExplore(conceptId) {
  const res = await fetch(`${BASE}/concept/explore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ concept_id: conceptId }),
  })
  return res.json()
}

export async function mergeConcepts(idA, idB) {
  const res = await fetch(`${BASE}/concept/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id_a: idA, id_b: idB }),
  })
  return res.json()
}

export async function undoMerge(mergeId) {
  const res = await fetch(`${BASE}/concept/undo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ merge_id: mergeId }),
  })
  return res.json()
}

// —— 概念图增强 ——
export async function getGlobalGraph(userId) {
  const url = userId ? `${BASE}/concept/global?user_id=${userId}` : `${BASE}/concept/global`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`global graph failed: ${res.status}`)
  return res.json()
}

export async function extendDomainGraph(sessionId, hops = 1) {
  const res = await fetch(`${BASE}/concept/extend?session_id=${sessionId}&hops=${hops}`, { method: 'POST' })
  if (!res.ok) throw new Error(`extend graph failed: ${res.status}`)
  return res.json()
}

export async function getConceptHistory(conceptId) {
  const res = await fetch(`${BASE}/concept/${conceptId}/history`)
  if (!res.ok) throw new Error(`history failed: ${res.status}`)
  return res.json()
}

export async function correctAnnotation(qaId, conceptId, action) {
  const res = await fetch(`${BASE}/concept/correct`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ qa_id: qaId, concept_id: conceptId, action }),
  })
  if (!res.ok) throw new Error(`correct failed: ${res.status}`)
  return res.json()
}

// —— 学习记忆 ——
// startQA 已支持 user_id（后端 QAStartRequest.user_id），这里补传
export async function startQAWithUser(question, userId, domainTag) {
  const res = await fetch(`${BASE}/qa/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, user_id: userId, domain_tag: domainTag }),
  })
  if (!res.ok) throw new Error(`start failed: ${res.status}`)
  return res.json()
}

export async function listSessions(userId, limit = 50) {
  const res = await fetch(`${BASE}/memory/users/${userId}/sessions?limit=${limit}`)
  if (!res.ok) throw new Error(`list sessions failed: ${res.status}`)
  return res.json()
}

export async function getSessionDetail(sessionId) {
  const res = await fetch(`${BASE}/memory/sessions/${sessionId}`)
  if (!res.ok) throw new Error(`session detail failed: ${res.status}`)
  return res.json()
}

// 恢复会话:并发取 session 详情(steps) + 概念图(概念详情),
// 合并成 { session_id, steps, conceptsById } 供 store 重建探索树。
// steps 只含 extracted_concept_ids,概念名/别名/explore_count 需从 graph 补全。
export async function resumeSession(sessionId) {
  const [detail, graph] = await Promise.all([
    getSessionDetail(sessionId),
    getGraph(sessionId).catch(() => ({ nodes: [], edges: [] })),
  ])
  const conceptsById = {}
  for (const n of (graph.nodes || [])) {
    conceptsById[n.concept_id] = {
      concept_id: n.concept_id,
      name: n.canonical_name,
      canonical_name: n.canonical_name,
      aliases: n.aliases || [],
      explore_count: n.explore_count || 0,
      understood: (n.explore_count || 0) >= 2,
    }
  }
  return {
    session_id: detail.session_id,
    steps: detail.steps || [],
    conceptsById,
  }
}

// -- 结构化导出：一次学习 -> md 手账 / json 备份（附件下载） --
export async function exportSession(sessionId, format = 'md') {
  const res = await fetch(`${BASE}/memory/sessions/${sessionId}/export?format=${format}`)
  if (!res.ok) throw new Error(`export failed: ${res.status}`)
  return res.blob()
}

// Blob 触发浏览器下载（文件名取自 Content-Disposition 的 RFC 5987 编码）
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

export async function getProfile(userId) {
  const res = await fetch(`${BASE}/memory/users/${userId}/profile`)
  if (!res.ok && res.status !== 404) throw new Error(`get profile failed: ${res.status}`)
  return res.status === 404 ? null : res.json()
}

export async function refreshProfile(userId, force = false) {
  const res = await fetch(
    `${BASE}/memory/users/${userId}/profile/refresh?force=${force}`,
    { method: 'POST' },
  )
  if (!res.ok) throw new Error(`refresh profile failed: ${res.status}`)
  return res.json()
}

// —— 学习材料导入 ——
export async function importMarkdown(userId, title, content) {
  const res = await fetch(`${BASE}/learning/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, title, content }),
  })
  if (!res.ok) throw new Error(`import failed: ${res.status}`)
  return res.json()
}

export async function uploadMarkdown(userId, file) {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${BASE}/learning/upload?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST', body: fd,
  })
  if (!res.ok) throw new Error(`upload failed: ${res.status}`)
  return res.json()
}

export async function listMaterials(userId) {
  const res = await fetch(`${BASE}/learning/materials?user_id=${encodeURIComponent(userId)}`)
  if (!res.ok) throw new Error(`list materials failed: ${res.status}`)
  return res.json()
}

export async function getMaterial(materialId) {
  const res = await fetch(`${BASE}/learning/materials/${materialId}`)
  if (!res.ok) throw new Error(`get material failed: ${res.status}`)
  return res.json()
}
