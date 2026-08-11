// QAStep 状态机前端 store：在途请求互斥 + 探索栈。
// 技术架构文档 3.2：前端在途请求互斥——同一 QAStep 未完成时禁用其他操作。
// 回上层为栈式回退，状态保留。

import { useSyncExternalStore } from 'react'

// 全局状态
const state = {
  sessionId: null,
  activeSessionId: null,  // 当前概念图所属会话
  // 探索栈：每层 { qa_id, question, answer, status, concepts, loading }
  stack: [],
  // 在途互斥：正在流式的 qa_id（其它操作禁用）
  inflight: null,
  // 概念图数据
  graph: { nodes: [], edges: [], views: {} },
  selectedOrigin: 'user_click',
}

const listeners = new Set()
function emit() { listeners.forEach((l) => l()) }
function set(partial) { Object.assign(state, partial); emit() }

export function getState() { return state }

// 在途互斥检查
export function isInflight(qaId) { return state.inflight === qaId }
export function canAct(qaId) { return state.inflight === null || state.inflight === qaId === false }
// 任意操作前校验：同一 QAStep 未完成时禁用其他操作
export function guardAction(qaId) {
  if (state.inflight && state.inflight !== qaId) return false
  return true
}

export function pushLayer(layer) {
  set({ stack: [...state.stack, layer], inflight: layer.qa_id })
}
export function setActiveSession(sid) { set({ activeSessionId: sid }) }
export function updateLayer(qaId, patch) {
  set({
    stack: state.stack.map((l) => (l.qa_id === qaId ? { ...l, ...patch } : l)),
  })
}
export function popToLayer(qaId) {
  // 出口2：栈式回退到指定层，保留该层及以下（状态保留）
  const idx = state.stack.findIndex((l) => l.qa_id === qaId)
  if (idx < 0) return
  set({ stack: state.stack.slice(0, idx + 1), inflight: null })
}
export function clearInflight() { set({ inflight: null }) }

// React 绑定
export function useStore(selector = (s) => s) {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => listeners.delete(cb) },
    () => selector(state),
  )
}
