import React, { useState } from 'react'
import * as api from '../api/client'
import {
  useStore, pushLayer, updateLayer, popToLayer, setLastViewed,
  clearInflight, guardAction, getState, setActiveSession, resetStack,
} from '../store/qaStore'

const MAX_DEPTH = 6

export default function TreeView() {
  const tree = useStore((s) => s.tree)
  const currentPath = useStore((s) => s.currentPath)
  const inflight = useStore((s) => s.inflight)
  const [question, setQuestion] = useState('')

  React.useEffect(() => {
    const handler = (e) => setQuestion(e.detail)
    window.addEventListener('starmind:prefillQuestion', handler)
    return () => window.removeEventListener('starmind:prefillQuestion', handler)
  }, [])

  async function startNewTree() {
    if (!question.trim()) return
    const uid = localStorage.getItem('starMindAgent.uid') || 'default'
    const qa = await api.startQA(question, null, null, uid)
    setActiveSession(qa.session_id)
    resetStack()  // 开新树：清空旧探索栈
    pushLayer({ qa_id: qa.qa_id, question, answer: '', status: 'generating', concepts: [], layer_summary: '', loading: true })
    subscribe(qa.qa_id)
    setQuestion('')
  }

  function subscribe(qaId) {
    api.subscribeStream(qaId, {
      answer_delta: (ev) => {
        const cur = getState().stack.find((l) => l.qa_id === qaId)
        updateLayer(qaId, { answer: (cur?.answer || '') + ev.text })
      },
      status: (ev) => updateLayer(qaId, { status: ev.status }),
      concepts: (ev) => updateLayer(qaId, { concepts: ev.concepts }),
      layer_summary: (ev) => updateLayer(qaId, { layer_summary: ev.layer_summary }),
      done: () => { updateLayer(qaId, { loading: false }); clearInflight() },
      error: () => { updateLayer(qaId, { loading: false }); clearInflight() },
    })
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
          />
        )}
      </nav>
    </aside>
  )
}

// 递归渲染树节点：当前 path 上的层高亮，子分支缩进
function TreeNode({ node, depth, currentPath, onSwitch }) {
  if (!node) return null
  const isCurrent = currentPath[currentPath.length - 1] === node.qa_id
  const onPath = currentPath.includes(node.qa_id)  // 在当前路径上
  const stemColor = isCurrent ? 'var(--active)' : onPath ? 'var(--rule)' : 'var(--rule-soft)'
  const hasChildren = node.children && node.children.length > 0
  return (
    <div style={styles.treeNode}>
      <button
        style={{
          ...styles.layerBtn,
          borderLeft: `2px solid ${stemColor}`,
          background: isCurrent ? 'var(--active-soft)' : 'transparent',
          opacity: isCurrent ? 1 : 0.72,
          fontWeight: isCurrent ? 500 : 400,
        }}
        onClick={() => onSwitch(node.qa_id)}
        title={isCurrent ? '当前层' : '点击切换到这层'}
      >
        <div style={styles.layerHead}>
          <span style={styles.depthTag}>L{depth}</span>
          <span style={styles.q}>{node.question}</span>
          {node.loading && <span style={styles.loadingDot} />}
          {hasChildren && <span style={styles.branchMark}>{hasChildren > 1 ? `┬${hasChildren}` : '└'}</span>}
        </div>
        {node.layer_summary && (
          <div style={styles.preview}>{node.layer_summary}</div>
        )}
      </button>
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
            />
          ))}
        </div>
      )}
    </div>
  )
}

const styles = {
  wrap: { width: 280, borderRight: '1px solid var(--rule)', padding: '14px 12px', overflowY: 'auto', background: 'var(--paper-soft)', flexShrink: 0 },
  inputRow: { display: 'flex', gap: 6, marginBottom: 18 },
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
  childrenWrap: { marginLeft: 14, display: 'flex', flexDirection: 'column', gap: 1, marginTop: 1 },
  layerBtn: {
    textAlign: 'left', background: 'transparent', border: 'none', borderLeft: '2px solid transparent',
    borderRadius: '0 var(--r-sm) var(--r-sm) 0', padding: '8px 10px 8px 12px',
    cursor: 'pointer', fontFamily: 'var(--sans)', color: 'var(--ink)', transition: 'background 0.15s, opacity 0.15s',
  },
  layerHead: { display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 },
  depthTag: {
    fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--active)', background: 'var(--paper)',
    borderRadius: 'var(--r-sm)', padding: '1px 5px', letterSpacing: '0.04em', flexShrink: 0,
  },
  q: {
    fontFamily: 'var(--serif)', fontSize: 12.5, fontWeight: 500, color: 'var(--ink)',
    lineHeight: 1.35, flex: 1, display: '-webkit-box', WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical', overflow: 'hidden',
  },
  loadingDot: { width: 5, height: 5, borderRadius: '50%', background: 'var(--active)', animation: 'pulse 1.4s ease-in-out infinite', flexShrink: 0, alignSelf: 'center' },
  preview: {
    fontSize: 10.5, color: 'var(--ink-soft)', fontStyle: 'italic', lineHeight: 1.45,
    fontFamily: 'var(--serif)', display: '-webkit-box', WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical', overflow: 'hidden', marginTop: 2,
  },
}
