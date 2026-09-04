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

// 恢复历史会话:用后端 steps(含 parent_qa_id)重建整棵探索树,
// 概念从 conceptsById 按 extracted_concept_ids 映射补全。
// currentPath 落到最深的叶子层,让用户从上次探索的最深处继续。
// 浏览历史重置为根→…→当前层的路径(丢弃旧的浏览历史)。
export function restoreSession(sessionId, steps, conceptsById) {
  if (!steps || steps.length === 0) return false

  // 按 parent_qa_id 建索引:parent_qa_id(null 当根) -> [step, ...]
  const byParent = new Map()
  const nullKey = '__root__'
  for (const st of steps) {
    const key = st.parent_qa_id || nullKey
    if (!byParent.has(key)) byParent.set(key, [])
    byParent.get(key).push(st)
  }

  // 把单个 step 转成 store 节点(概念按 id 映射,保留本层标识顺序)
  const toNode = (st) => {
    const conceptIds = st.extracted_concept_ids || []
    const concepts = conceptIds
      .map((id) => conceptsById[id])
      .filter(Boolean)
    // 去重(后端可能同 id 重复标识)
    const seen = new Set()
    const uniq = concepts.filter((c) => {
      if (seen.has(c.concept_id)) return false
      seen.add(c.concept_id)
      return true
    })
    return {
      qa_id: st.qa_id,
      question: st.question,
      answer: st.answer || '',
      status: st.status || 'waiting',
      depth: st.depth || 1,
      concepts: uniq,
      layer_summary: st.layer_summary || '',
      checked: !!st.checked,
      loading: false,
      children: [],
    }
  }

  // 递归建树:从 parent 的 children 列表构建子树
  const buildSubtree = (node) => {
    const kids = byParent.get(node.qa_id) || []
    for (const k of kids) {
      const child = toNode(k)
      node.children.push(child)
      buildSubtree(child)
    }
    return node
  }

  const roots = byParent.get(nullKey) || []
  if (roots.length === 0) return false
  const rootNode = buildSubtree(toNode(roots[0]))

  // 找最深叶子作为 currentPath 末端(从根 DFS 取最深路径)
  let deepest = { path: [rootNode.qa_id], node: rootNode }
  const dfs = (node, path) => {
    if (path.length > deepest.path.length) deepest = { path: [...path], node }
    for (const c of (node.children || [])) {
      dfs(c, [...path, c.qa_id])
    }
  }
  dfs(rootNode, [rootNode.qa_id])

  set({
    sessionId,
    activeSessionId: sessionId,
    tree: rootNode,
    currentPath: deepest.path,
    inflight: null,
    // 浏览历史重置为这条根→当前层的路径,指针指末端。
    // 深路径同样受 30 步滑动窗口约束(保留最近的 30 步)
    history: deepest.path.length > MAX_HISTORY
      ? deepest.path.slice(deepest.path.length - MAX_HISTORY)
      : [...deepest.path],
    historyIdx: Math.min(deepest.path.length, MAX_HISTORY) - 1,
  })
  return true
}

// 更新某层（按 qa_id 遍历树找节点并 patch）
export function updateLayer(qaId, patch) {
  const node = findNode(qaId)
  if (node) {
    Object.assign(node, patch)
    emit()
  }
}

// 原地 patch 后刷新：复制根节点换掉 state.tree 引用。
// useSyncExternalStore 按 getSnapshot 返回值是否相等决定是否重渲染，
// 只 emit() 而引用不变时订阅 s.tree 的组件（TreeView/ReadingPane 等）
// 快照相等会跳过刷新——勾选 check 后 UI 冻住就是这个坑。
function bumpTree() {
  if (state.tree) state.tree = { ...state.tree }
  emit()
}

// 勾选/取消勾选某层学习完成度：乐观更新本地节点，失败回滚并抛错
export async function toggleChecked(qaId) {
  const node = findNode(qaId)
  if (!node) return
  const prev = !!node.checked
  const next = !prev
  node.checked = next
  bumpTree()
  try {
    const { setChecked } = await import('../api/client')
    await setChecked(qaId, next)
  } catch (err) {
    node.checked = prev
    bumpTree()
    console.error('[toggleChecked] 失败:', err)
  }
}

// —— 阅读进度驱动的智能完成 ——
// 人类阅读速度:中文约 420 字/分钟(7 字/秒),低于上限防快滚刷满。
export const READ_SPEED_CPS = 7
// 视觉中心位置与速度折算都达到 90% 即判定"读完",层自动完成。
export const READ_DONE_THRESHOLD = 0.9

// 上报某层阅读进度:position = 视觉中心相对正文顶部的已过比例(0-1,单调取最大),
// visible = 页面是否前台可见。停留时长由 ReadingPane 心跳累计(秒)。
// 双信号取 min:位置到了但没停留够(快滚)不算读完,反之亦然。
// 进度达阈值:层自动标记完成(仅层 checked,不动概念——
// 概念是否"已理解"与阅读进度无关,由下钻探索/复习卡片等用户行为表达)。
export function reportReadProgress(qaId, { position, visible }) {
  const node = findNode(qaId)
  if (!node || !node.answer) return
  const st = node.readProgress || { elapsed: 0, maxPos: 0 }
  const maxPos = Math.max(st.maxPos, Math.min(Math.max(position || 0, 0), 1))
  node.readProgress = {
    ...st,
    maxPos,
    visible: !!visible,
    lastBeat: Date.now(),
  }
  recomputeReadDone(qaId, node)
  bumpTree()
}

// 心跳:每秒由 ReadingPane 调一次,前台可见才累计停留时长。
export function tickReadTime(qaId, visible) {
  const node = findNode(qaId)
  if (!node) return
  const st = node.readProgress || { elapsed: 0, maxPos: 0, lastBeat: Date.now() }
  const now = Date.now()
  // 单拍封顶 5s:正常 1s 心跳实报实记;定时器被节流(后台/省电)时
  // 拍间隔拉长,按 5s 封顶补记,避免读着读着进度冻住
  const delta = visible ? Math.min(Math.max(now - st.lastBeat, 0), 5000) / 1000 : 0
  node.readProgress = { ...st, visible: !!visible, lastBeat: now, elapsed: st.elapsed + delta }
  recomputeReadDone(qaId, node)
  bumpTree()
}

// 双信号折算已读比例并判定完成(只前进不倒退;手动勾选优先)
function recomputeReadDone(qaId, node) {
  const st = node.readProgress
  if (!st) return
  const estChars = Math.max(answerCharCount(node.answer), 1)
  const byTime = Math.min((st.elapsed * READ_SPEED_CPS) / estChars, 1)
  const pct = Math.min(st.maxPos, byTime)
  node.readPct = pct
  if (pct >= READ_DONE_THRESHOLD && !node.checked && !node.autoDone) {
    node.autoDone = true
    markLayerDone(qaId, true)
  }
}

// 层自动完成:阅读进度读满时仅置层 checked(落库),不动概念
async function markLayerDone(qaId, done) {
  const node = findNode(qaId)
  if (!node) return
  node.checked = done
  bumpTree()
  try {
    const { setChecked } = await import('../api/client')
    await setChecked(qaId, done)
  } catch (err) {
    console.error('[markLayerDone] 落库失败:', err)
  }
}

// 正文有效字数:剥离 markdown 符号后的近似字数(中文正文 1 字 1 计)
function answerCharCount(text) {
  return (text || '')
    .replace(/[#*_>`~\[\]()!|:\-]/g, '')
    .replace(/\s+/g, '')
    .length
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

// 删除层：后端级联删子层成功后本地剪枝。
// - 删的是根：整棵树清空（回提问空态）
// - 删的是路径上的层（含当前层）：currentPath 收回到被删层的父层
// - 浏览历史里被删子树的 qa_id 一并剔除（防前进/后退跳到已删层）
export function removeLayer(qaId) {
  if (!state.tree) return
  if (state.tree.qa_id === qaId) {
    set({ tree: null, currentPath: [], inflight: null, history: [], historyIdx: -1 })
    return
  }
  // 从父节点的 children 里摘除
  const walk = (node) => {
    for (const child of (node.children || [])) {
      if (child.qa_id === qaId) {
        node.children = node.children.filter((c) => c.qa_id !== qaId)
        return true
      }
      if (walk(child)) return true
    }
    return false
  }
  if (!walk(state.tree)) return
  // 被删子树内的 qa_id 集合（剪枝后已不可达,按 currentPath 是否含该层判断即可）
  const removedFromPath = state.currentPath.includes(qaId)
  if (removedFromPath) {
    // 收回到父层:currentPath 截到被删层的前一项
    const idx = state.currentPath.indexOf(qaId)
    const nextPath = state.currentPath.slice(0, Math.max(idx, 1))
    set({ currentPath: nextPath, inflight: null })
  }
  // 历史剔除被删层及其之后到不了的位置——历史是线性序列,
  // 被删 qa_id 直接从 history 里去掉,指针重排
  const nh = state.history.filter((id) => id !== qaId)
  const cur = state.currentPath[state.currentPath.length - 1]
  let nIdx = nh.lastIndexOf(cur)
  if (nIdx === -1) nIdx = Math.min(state.historyIdx, nh.length - 1)
  set({ history: nh, historyIdx: nIdx })
  bumpTree()
}

// 重问：本地重置该层现场（新问题 + 清回答/概念/候选）并置 inflight。
// 后端 reask 端点改库由调用方负责；之后调用方重新订阅 stream 收新流。
export function reaskLayer(qaId, newQuestion) {
  const node = findNode(qaId)
  if (!node) return
  Object.assign(node, {
    question: newQuestion,
    answer: '',
    concepts: [],
    candidates: [],
    layer_summary: '',
    searchSources: null,
    error: null,
    readProgress: null,
    readPct: null,
    autoDone: false,
    loading: true,
    status: 'generating',
  })
  bumpTree()
  set({ inflight: qaId })
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
