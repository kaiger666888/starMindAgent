// QAStep 状态机前端 store：在途请求互斥 + 探索树 + 浏览历史。
// 树状分支结构：一个父层下可有多个子层分支。currentPath = 根到当前层 qa_id 路径。
// 浏览历史(独立于树导航):记录访问过的 qa_id 线性序列 + 指针,
//   前进/后退在历史里移动(浏览器式),上限 30 步滑动窗口。
//   下钻/切层时 push 历史(丢弃"未来"),与树导航完全解耦。

import { useSyncExternalStore } from 'react'

// 浏览历史上限:最多保留 30 步,超过丢弃最旧(滑动窗口)
const MAX_HISTORY = 30

// 全局状态
const state = {
  sessionId: null,
  activeSessionId: null,  // 当前概念图所属会话
  // 探索树根节点: { qa_id, question, answer, status, concepts, layer_summary, loading, children:[] }
  tree: null,
  // 当前路径: [root_qa_id, ..., current_qa_id] 从根到当前层
  currentPath: [],
  // 浏览历史: 记录访问过的 qa_id 序列(独立于树结构)
  history: [],
  // 历史指针: 当前在 history 中的位置,-1 表示空
  historyIdx: -1,
  // 在途互斥：正在流式的 qa_id（其它操作禁用）
  inflight: null,
  // 概念图数据
  graph: { nodes: [], edges: [], views: {} },
  selectedOrigin: 'user_click',
}

const listeners = new Set()
function emit() { listeners.forEach((l) => l()) }
function set(partial) { Object.assign(state, partial); emit() }

// 往浏览历史 push 一个 qa_id:丢弃指针之后的"未来",再加新项,超 30 步丢最旧
// 注意:仅记录访问,不改变 currentPath(树导航由调用方负责)
function pushHistory(qaId) {
  if (!qaId) return
  const base = state.history.slice(0, state.historyIdx + 1)
  // 若与上一条相同则不重复记录
  if (base.length > 0 && base[base.length - 1] === qaId) {
    return  // 已是最新一条,不重复
  }
  let next = [...base, qaId]
  let idx = next.length - 1
  // 滑动窗口:超 MAX_HISTORY 丢弃最旧
  if (next.length > MAX_HISTORY) {
    next = next.slice(next.length - MAX_HISTORY)
    idx = next.length - 1
  }
  set({ history: next, historyIdx: idx })
}

export function getState() { return state }

// 按 qa_id 在树里找节点（DFS）
export function findNode(qaId, node = state.tree) {
  if (!node) return null
  if (node.qa_id === qaId) return node
  for (const child of (node.children || [])) {
    const found = findNode(qaId, child)
    if (found) return found
  }
  return null
}

// 当前层 = currentPath 末尾的节点
export function getCurrentLayer() {
  if (!state.currentPath.length || !state.tree) return null
  const curId = state.currentPath[state.currentPath.length - 1]
  return findNode(curId)
}

// 当前层的深度 = currentPath 长度
export function getCurrentDepth() {
  return state.currentPath.length
}

// 路径上的所有节点（根到当前）
export function getPathNodes() {
  return state.currentPath.map(id => findNode(id)).filter(Boolean)
}

// 计算 qa_id 的路径（根到该节点）
function pathToNode(qaId, node = state.tree, path = []) {
  if (!node) return []
  const newPath = [...path, node.qa_id]
  if (node.qa_id === qaId) return newPath
  for (const child of (node.children || [])) {
    const found = pathToNode(qaId, child, newPath)
    if (found.length) return found
  }
  return []
}

// 在途互斥检查
export function isInflight(qaId) { return state.inflight === qaId }
export function canAct(qaId) { return state.inflight === null || state.inflight === qaId === false }
export function guardAction(qaId) {
  if (state.inflight && state.inflight !== qaId) return false
  return true
}

// 开新树：清空旧探索树 + 浏览历史
export function resetStack() {
  set({ tree: null, currentPath: [], inflight: null, history: [], historyIdx: -1 })
}

// 开第一层（根）：建树
export function setRoot(layer) {
  const node = { ...layer, children: [] }
  set({ tree: node, currentPath: [node.qa_id], inflight: node.qa_id })
  pushHistory(node.qa_id)
}

// 下钻：在 parentQaId 下加子层（兄弟分支并存，不替换）
export function addChildLayer(parentQaId, layer) {
  const parent = findNode(parentQaId)
  if (!parent) return
  const node = { ...layer, children: [] }
  if (!parent.children) parent.children = []
  parent.children.push(node)
  // currentPath = 根到 parent 的路径 + 新节点
  const parentPath = pathToNode(parentQaId)
  set({ currentPath: [...parentPath, node.qa_id], inflight: node.qa_id })
  pushHistory(node.qa_id)
}

export function setActiveSession(sid) { set({ activeSessionId: sid }) }

// 更新某层（按 qa_id 遍历树找节点并 patch）
export function updateLayer(qaId, patch) {
  const node = findNode(qaId)
  if (node) {
    Object.assign(node, patch)
    emit()
  }
}

// 记录回上层时"上次看的概念"
export function setLastViewed(qaId, conceptId) {
  const node = findNode(qaId)
  if (node) {
    node.last_viewed_concept = conceptId
    emit()
  }
}

// 出口2：回上层——沿 path 向上切到指定层（子分支保留在树里）
export function popToLayer(qaId) {
  const path = pathToNode(qaId)
  if (path.length) {
    set({ currentPath: path, inflight: null })
    pushHistory(qaId)
  }
}

// 翻页:后退/前进——在浏览历史里移动(浏览器式),与树导航解耦
// 后退:历史指针 -1,切到上一个访问过的 qa_id
export function goBack() {
  if (state.historyIdx > 0) {
    const idx = state.historyIdx - 1
    const qaId = state.history[idx]
    const path = pathToNode(qaId)
    if (path.length) set({ currentPath: path, historyIdx: idx, inflight: null })
  }
}
// 前进:历史指针 +1,切到下一个访问过的 qa_id(若有"未来")
export function goForward() {
  if (state.historyIdx < state.history.length - 1) {
    const idx = state.historyIdx + 1
    const qaId = state.history[idx]
    const path = pathToNode(qaId)
    if (path.length) set({ currentPath: path, historyIdx: idx, inflight: null })
  }
}
// 历史导航能力查询(供 UI 禁用态)
export function canGoBack() { return state.historyIdx > 0 }
export function canGoForward() { return state.historyIdx < state.history.length - 1 }

export function clearInflight() { set({ inflight: null }) }

// 兼容旧 API：pushLayer = 若无树建根，否则在当前层下加子层
export function pushLayer(layer) {
  if (!state.tree) {
    setRoot(layer)
    return
  }
  const parentId = state.currentPath[state.currentPath.length - 1]
  addChildLayer(parentId, layer)
}

// React 绑定
export function useStore(selector = (s) => s) {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => listeners.delete(cb) },
    () => selector(state),
  )
}
