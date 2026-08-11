// 后端 API 客户端：QAStep 状态机三个出口 + concept 服务。
// 严格对应后端路由 app/api/routes_*.py

const BASE = ''  // 通过 vite proxy 转发到 :8000

export async function startQA(question, sessionId, domainTag) {
  const res = await fetch(`${BASE}/qa/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sessionId, domain_tag: domainTag }),
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

// 出口1：点击概念下钻
export async function drillDown(parentQaId, conceptId, question) {
  const res = await fetch(`${BASE}/qa/${parentQaId}/drilldown`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ concept_id: conceptId, question }),
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
