import React, { useState, useRef } from 'react'
import * as api from '../api/client'
import {
  useStore, pushLayer, updateLayer, popToLayer, setLastViewed,
  clearInflight, guardAction, setActiveSession, resetStack, findNode, setRoot,
  toggleChecked,
} from '../store/qaStore'
import ConceptSummary from './ConceptSummary'

const MAX_DEPTH = 6

// 节点类型与显示标签：概念层只显概念名（名词骨架），提问层显原始问题。
// 回退链：displayLabel 字段 -> question 按下钻包装格式「深入解释「X」」解析
// -> origin 无标记的旧/根层，显示 question 原文
function parseLayerInfo(node) {
  const q = node.question || ''
  if (node.origin === 'ask') return { kind: 'ask', label: q }
  if (node.displayLabel) return { kind: 'concept', label: node.displayLabel }
  const m = q.match(/^深入解释「(.+?)」/)
  if (m) return { kind: 'concept', label: m[1] }
  return { kind: 'plain', label: q }
}

export default function TreeView() {
  const tree = useStore((s) => s.tree)
  const currentPath = useStore((s) => s.currentPath)
  const inflight = useStore((s) => s.inflight)
  const [question, setQuestion] = useState('')
  const [importing, setImporting] = useState(false)
  const fileInputRef = useRef(null)

  React.useEffect(() => {
    const handler = (e) => setQuestion(e.detail)
    window.addEventListener('starmind:prefillQuestion', handler)
    return () => window.removeEventListener('starmind:prefillQuestion', handler)
  }, [])

  async function startNewTree() {
    if (!question.trim()) return
    try {
      const uid = localStorage.getItem('starMindAgent.uid') || 'default'
      const qa = await api.startQA(question, null, null, uid)
      setActiveSession(qa.session_id)
      resetStack()  // 开新树：清空旧探索栈
      pushLayer({ qa_id: qa.qa_id, question, answer: '', status: 'generating', concepts: [], layer_summary: '', loading: true })
      subscribe(qa.qa_id)
      setQuestion('')
    } catch (err) {
      console.error('[startNewTree] 失败:', err)
    }
  }

  function subscribe(qaId) {
    api.subscribeStream(qaId, {
      answer_delta: (ev) => {
        const cur = findNode(qaId)
        updateLayer(qaId, { answer: (cur?.answer || '') + ev.text })
      },
      status: (ev) => updateLayer(qaId, { status: ev.status }),
      concepts: (ev) => updateLayer(qaId, { concepts: ev.concepts }),
      concept_candidates: (ev) => {
        const cur = findNode(qaId)
        const prev = cur?.candidates || []
        updateLayer(qaId, { candidates: [...prev, ...ev.concepts] })
      },
      search_sources: (ev) => updateLayer(qaId, { searchSources: ev.sources }),
      layer_summary: (ev) => updateLayer(qaId, { layer_summary: ev.layer_summary }),
      done: () => { updateLayer(qaId, { loading: false }); clearInflight() },
      error: () => { updateLayer(qaId, { loading: false }); clearInflight() },
    })
  }

  // 导入 markdown：上传文件，建 L0 根层显示文件内容
  async function onImportFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true)
    try {
      const uid = localStorage.getItem('starMindAgent.uid') || 'default'
      const result = await api.uploadMarkdown(uid, file)
      setActiveSession(result.qa_id)  // 用 L0 qa_id 作 active session
      resetStack()
      // 建 L0 根层：question=文件名，answer=文件全文，concepts=抽取的
      setRoot({
        qa_id: result.qa_id,
        question: result.title,
        answer: result.content_plain,
        status: 'waiting',
        concepts: result.concepts || [],
        layer_summary: '',
        loading: false,
      })
      // 如果后端已抽取概念，updateLayer 已含；否则本地补抽（省略，L0 显示全文即可）
    } catch (err) {
      alert(`导入失败: ${err.message}`)
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  return (
    <aside style={styles.wrap}>
      <div style={styles.inputRow}>
        <input
          style={styles.input}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="提一个问题…"
          onKeyDown={(e) => e.key === 'Enter' && startNewTree()}
        />
        <button style={styles.btn} onClick={startNewTree} disabled={!!inflight}>开新树</button>
      </div>
      <div style={styles.importRow}>
        <input
          ref={fileInputRef}
          type="file"
          accept=".md,.markdown,.txt"
          style={{ display: 'none' }}
          onChange={onImportFile}
        />
        <button
          style={styles.importBtn}
          onClick={() => fileInputRef.current?.click()}
          disabled={!!inflight || importing}
        >
          {importing ? '导入中…' : '导入学习文件'}
        </button>
      </div>

      {!tree && (
        <div style={styles.empty}>
          <div style={styles.emptyStem} aria-hidden="true" />
          <div>提一个问题，让概念生长成树</div>
        </div>
      )}

      <nav style={styles.tree} aria-label="探索树">
        {tree && (
          <TreeNode
            node={tree}
            depth={1}
            currentPath={currentPath}
            onSwitch={(qid) => popToLayer(qid)}
            onAsk={(node) => {
              // 追问前先切到该层（AskDialog 挂在 ReadingLayer，层对了才有上下文）
              popToLayer(node.qa_id)
              window.dispatchEvent(new CustomEvent('starmind:askSelection', {
                detail: { kind: 'layer', text: node.question, label: '就这层追问' },
              }))
            }}
          />
        )}
      </nav>

      <ConceptSummary />
    </aside>
  )
}

// 递归渲染树节点：当前 path 上的层高亮，子分支缩进
function TreeNode({ node, depth, currentPath, onSwitch, onAsk }) {
  if (!node) return null
  const isCurrent = currentPath[currentPath.length - 1] === node.qa_id
  const onPath = currentPath.includes(node.qa_id)  // 在当前路径上
  const stemColor = isCurrent ? 'var(--active)' : onPath ? 'var(--rule)' : 'var(--rule-soft)'
  const hasChildren = node.children && node.children.length > 0
  // 节点类型与显示标签（结构即信息）：
  //   概念下钻层 -> 只显示概念名（名词骨架，树的主体）
  //   提问层     -> 显示原始问题 + 「问」章（穿插的追问插页）
  // 旧数据回退：question 以「深入解释「X」」开头的解析出概念名
  const layerInfo = parseLayerInfo(node)
  const isAsk = layerInfo.kind === 'ask'
  // 该层概念完成度:已理解数 / 总数(无概念则不显示)
  const concepts = node.concepts || []
  const understoodN = concepts.filter((c) => c.understood || (c.explore_count >= 2)).length
  const layerPct = concepts.length > 0 ? understoodN / concepts.length : null
  // 学习进度(背景进度条):手动勾选 = 100%;否则回退概念理解比例
  const checked = !!node.checked
  const progress = checked ? 1 : (layerPct ?? 0)
  // 进度条式背景:深绿从左向右按 progress 增长;勾选满格。
  // 有进度(>0)才渲染填充,零进度保持透明避免整片绿底。
  const progressBg = progress > 0
    ? `linear-gradient(to right, rgba(47,107,79,${checked ? 0.32 : 0.18}) 0%, rgba(47,107,79,${checked ? 0.32 : 0.18}) ${progress * 100}%, transparent ${progress * 100}%)`
    : 'transparent'
  // 下钻准备度:后端为这层检索材料准备好了多少(context.preparation 0-1)
  // 优先于概念完成度展示——准备度反映"材料就绪",概念完成度是回退
  const prep = node.context?.preparation
  const hasPrep = typeof prep === 'number' && prep > 0
  return (
    <div style={styles.treeNode}>
      <div style={styles.layerRow}>
        <button
          aria-label={checked ? '取消完成标记' : '标记已完成'}
          style={{
            ...styles.checkBtn,
            ...(checked ? styles.checkBtnDone : null),
          }}
          onClick={(e) => { e.stopPropagation(); toggleChecked(node.qa_id) }}
          title={checked ? '已完成(点击取消)' : '标记已完成'}
        >
          {checked && <svg width="9" height="8" viewBox="0 0 10 8" aria-hidden="true"><polyline points="1,4 3.8,6.5 9,1" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>}
        </button>
        <button
          style={{
            ...styles.layerBtn,
            borderLeft: `2px solid ${checked ? 'var(--done)' : stemColor}`,
            background: progressBg,
            opacity: isCurrent ? 1 : 0.72,
            fontWeight: isCurrent ? 500 : 400,
            flex: 1, minWidth: 0,
            transition: 'background 0.4s ease, border-color 0.2s, opacity 0.15s',
          }}
          onClick={() => onSwitch(node.qa_id)}
          title={isCurrent ? '当前层' : (layerInfo.label !== node.question ? node.question : '点击切换到这层')}
        >
          <div style={styles.layerHead}>
            <span style={styles.depthTag}>L{depth}</span>
            {isAsk && <span style={styles.askMark} title="提问层">问</span>}
            <span style={{ ...styles.q, ...(isAsk ? styles.qAsk : styles.qConcept) }}>
              {layerInfo.label}
            </span>
            {node.loading && <span style={styles.loadingDot} />}
            {hasChildren && <span style={styles.branchMark}>{hasChildren > 1 ? `┬${hasChildren}` : '└'}</span>}
          </div>
          {node.layer_summary && (
            <div style={styles.preview}>{node.layer_summary}</div>
          )}
          {/* 下钻准备度进度条:后端为这层关键词检索材料准备好了多少。
              准备中=墨蓝(活跃信号),就绪(100%)=陶土棕(沉淀)。
              无 context 的层(提问树根/未导入材料)回退到概念完成度。 */}
          {hasPrep ? (
            <div style={styles.layerProgRow}>
              <span style={styles.layerProgLabel}>
                准备 {Math.round(prep * 100)}%
              </span>
              <span style={styles.layerProgBar}>
                <span style={{
                  ...styles.layerProgFill,
                  width: `${prep * 100}%`,
                  background: prep >= 1 ? 'var(--settled)' : 'var(--active)',
                }} />
              </span>
            </div>
          ) : layerPct !== null && !checked && (
            <div style={styles.layerProgRow}>
              <span style={styles.layerProgLabel}>
                概念 {understoodN}/{concepts.length}
              </span>
              <span style={styles.layerProgBar}>
                <span style={{ ...styles.layerProgFill, width: `${layerPct * 100}%` }} />
              </span>
            </div>
          )}
          {checked && (
            <div style={styles.doneRow}>
              <span style={styles.doneLabel}>已完成</span>
            </div>
          )}
        </button>
        {/* 追问入口：hover 露出，点击就这层提问（不切层） */}
        <button
          style={styles.treeAskBtn}
          onClick={(e) => {
            e.stopPropagation()
            onAsk(node)
          }}
          title="就这层追问"
          aria-label={`就第 ${depth} 层追问`}
        >问</button>
      </div>
      {/* 子分支：当前路径上的层展开 children，非路径上的层也展开（树导航可见） */}
      {hasChildren && (
        <div style={styles.childrenWrap}>
          {node.children.map((child, i) => (
            <TreeNode
              key={child.qa_id || i}
              node={child}
              depth={depth + 1}
              currentPath={currentPath}
              onSwitch={onSwitch}
              onAsk={onAsk}
            />
          ))}
        </div>
      )}
    </div>
  )
}

const styles = {
  wrap: { width: 280, borderRight: '1px solid var(--rule)', padding: '14px 12px', overflowY: 'auto', background: 'var(--paper-soft)', flexShrink: 0 },
  inputRow: { display: 'flex', gap: 6, marginBottom: 10 },
  importRow: { marginBottom: 18 },
  importBtn: {
    width: '100%', padding: '7px 12px', background: 'var(--paper)',
    color: 'var(--settled)', border: '1px dashed var(--settled)',
    borderRadius: 'var(--r-sm)', cursor: 'pointer', fontFamily: 'var(--sans)',
    fontSize: 12, transition: 'all 0.15s',
  },
  input: {
    flex: 1, padding: '8px 10px', border: '1px solid var(--rule)', borderRadius: 'var(--r-sm)',
    background: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--ink)',
    outline: 'none', transition: 'border-color 0.15s',
  },
  btn: {
    padding: '8px 14px', background: 'var(--active)', color: '#fff', border: 'none',
    borderRadius: 'var(--r-sm)', cursor: 'pointer', fontFamily: 'var(--sans)',
    fontSize: 12, fontWeight: 500, transition: 'background 0.15s',
  },
  empty: {
    fontSize: 11.5, color: 'var(--ink-faint)', padding: '20px 8px',
    textAlign: 'center', fontFamily: 'var(--serif)', fontStyle: 'italic',
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
  },
  emptyStem: { width: 2, height: 22, borderRadius: 1, background: 'var(--rule)' },
  tree: { display: 'flex', flexDirection: 'column', gap: 1 },
  treeNode: { position: 'relative' },
  // 层条目行：按钮 + 追问入口并排（追问按钮 hover 露出，平时占位不跳版式）
  layerRow: { display: 'flex', alignItems: 'stretch' },
  // 树上"问"入口：小方章（与 AskDialog 的印章呼应），hover 才显形--
  // 树是导航区，追问是次级动作，不能让每个节点常驻一个按钮
  treeAskBtn: {
    width: 20, flexShrink: 0, alignSelf: 'center', marginLeft: 4,
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    background: 'transparent', border: '1px solid transparent',
    color: 'var(--active)', borderRadius: 'var(--r-sm)',
    fontFamily: 'var(--serif)', fontSize: 11, fontWeight: 600,
    cursor: 'pointer', opacity: 0, transition: 'opacity 0.15s, border-color 0.15s',
  },
  childrenWrap: { marginLeft: 14, display: 'flex', flexDirection: 'column', gap: 1, marginTop: 1 },
  layerBtn: {
    textAlign: 'left', background: 'transparent', border: 'none', borderLeft: '2px solid transparent',
    borderRadius: '0 var(--r-sm) var(--r-sm) 0', padding: '8px 10px 8px 12px',
    cursor: 'pointer', fontFamily: 'var(--sans)', color: 'var(--ink)', transition: 'background 0.15s, opacity 0.15s',
  },
  layerHead: { display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 },
  // 学习完成度 check 框：行首小方块，勾选后深绿填充 + 白对钩
  checkBtn: {
    width: 16, height: 16, flexShrink: 0, alignSelf: 'center', marginLeft: 2,
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    background: 'var(--paper)', border: '1px solid var(--rule)',
    borderRadius: 'var(--r-sm)', cursor: 'pointer', padding: 0,
    transition: 'background 0.2s, border-color 0.2s',
  },
  checkBtnDone: {
    background: 'var(--done)', borderColor: 'var(--done)',
  },
  doneRow: { marginTop: 4 },
  doneLabel: {
    fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--done)',
    letterSpacing: '0.04em',
  },
  depthTag: {
    fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--active)', background: 'var(--paper)',
    borderRadius: 'var(--r-sm)', padding: '1px 5px', letterSpacing: '0.04em', flexShrink: 0,
  },
  // 提问层「问」章：与 AskDialog 印章同构（描边小方块衬线字），墨蓝描边
  askMark: {
    width: 14, height: 14, display: 'inline-flex', flexShrink: 0,
    alignItems: 'center', justifyContent: 'center',
    border: '1px solid var(--active)', color: 'var(--active)',
    borderRadius: 'var(--r-sm)', fontFamily: 'var(--serif)',
    fontSize: 9, fontWeight: 600, lineHeight: 1, alignSelf: 'center',
  },
  q: {
    fontFamily: 'var(--serif)', fontSize: 12.5, fontWeight: 500, color: 'var(--ink)',
    lineHeight: 1.35, flex: 1, display: '-webkit-box', WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical', overflow: 'hidden',
  },
  // 概念层标签：名词骨架,单行截断(概念名短,两行占位是浪费);
  // 字重 600 比提问层重一档 -- 名词是树的主干,问句是插页
  qConcept: {
    fontWeight: 600, WebkitLineClamp: 1, whiteSpace: 'nowrap',
  },
  // 提问层标签:常规字重,衬线斜度感由内容(问句)自带,不加修饰
  qAsk: {
    fontStyle: 'normal',
  },
  loadingDot: { width: 5, height: 5, borderRadius: '50%', background: 'var(--active)', animation: 'pulse 1.4s ease-in-out infinite', flexShrink: 0, alignSelf: 'center' },
  preview: {
    fontSize: 10.5, color: 'var(--ink-soft)', fontStyle: 'italic', lineHeight: 1.45,
    fontFamily: 'var(--serif)', display: '-webkit-box', WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical', overflow: 'hidden', marginTop: 2,
  },
  // 该层概念完成度进度条
  layerProgRow: {
    display: 'flex', alignItems: 'center', gap: 6, marginTop: 4,
  },
  layerProgLabel: {
    fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--ink-faint)',
    letterSpacing: '0.02em', flexShrink: 0,
  },
  layerProgBar: {
    flex: 1, height: 2, background: 'var(--rule-soft)', borderRadius: 1, overflow: 'hidden',
  },
  layerProgFill: {
    height: '100%', background: 'var(--settled)', borderRadius: 1,
    transition: 'width 0.4s ease',
  },
}
