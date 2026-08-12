// QAStep 状态机前端 store：在途请求互斥 + 探索栈。
// 技术架构文档 3.2：前端在途请求互斥——同一 QAStep 未完成时禁用其他操作。
// 回上层为栈式回退，状态保留。

import { useSyncExternalStore } from 'react'

// 全局状态
const state = {
  sessionId: null,
  activeSessionId: null,  // 当前概念图所属会话
  // 探索栈：每层 { qa_id, question, answer, status, concepts, loading }
  // stack 永远保留全部探索历史，回上层不删层
  stack: [],
  // 当前查看的层索引（默认栈顶）。回上层只移动它，不删 stack。
  currentIdx: -1,
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
// 当前层（栈顶或 currentIdx 指向的层）
export function getCurrentLayer() {
  if (state.currentIdx >= 0 && state.currentIdx < state.stack.length) {
    return state.stack[state.currentIdx]
  }
  return state.stack[state.stack.length - 1]
}

// 在途互斥检查
export function isInflight(qaId) { return state.inflight === qaId }
export function canAct(qaId) { return state.inflight === null || state.inflight === qaId === false }
// 任意操作前校验：同一 QAStep 未完成时禁用其他操作
export function guardAction(qaId) {
  if (state.inflight && state.inflight !== qaId) return false
  return true
}

export function pushLayer(layer) {
  // 下钻：新层入栈，currentIdx 指向新层（栈顶）
  set({ stack: [...state.stack, layer], currentIdx: state.stack.length, inflight: layer.qa_id })
}
// 开新树：清空旧探索栈，重置 currentIdx
export function resetStack() {
  set({ stack: [], currentIdx: -1, inflight: null })
}
export function setActiveSession(sid) { set({ activeSessionId: sid }) }
export function updateLayer(qaId, patch) {
  set({
    stack: state.stack.map((l) => (l.qa_id === qaId ? { ...l, ...patch } : l)),
  })
}
// 记录回上层时"上次看的概念"——高亮提示探索断点（需求二节）
export function setLastViewed(qaId, conceptId) {
  set({
    stack: state.stack.map((l) =>
      l.qa_id === qaId ? { ...l, last_viewed_concept: conceptId } : l
    ),
  })
}
// 出口2：回上层——只切 currentIdx，保留所有探索历史（不删层）
export function popToLayer(qaId) {
  const idx = state.stack.findIndex((l) => l.qa_id === qaId)
  if (idx < 0) return
  set({ currentIdx: idx, inflight: null })
}
export function clearInflight() { set({ inflight: null }) }

// React 绑定
export function useStore(selector = (s) => s) {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => listeners.delete(cb) },
    () => selector(state),
  )
}
