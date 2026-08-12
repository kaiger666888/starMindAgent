import React, { useState } from 'react'
import * as api from '../api/client'
import {
  useStore, pushLayer, updateLayer, popToLayer, setLastViewed,
  clearInflight, guardAction, getState, setActiveSession,
} from '../store/qaStore'

const MAX_DEPTH = 6  // 膨胀控制硬上限

// 着色档(概念热度,对数1/2/4/8)
const TIER_COLOR = {
  gray: 'var(--tier-gray)', green: 'var(--tier-green)',
  red_1: 'var(--tier-red-1)', red_2: 'var(--tier-red-2)',
  red_3: 'var(--tier-red-3)', red_4: 'var(--tier-red-4)',
}
function tierForCount(c) {
  if (c <= 0) return 'gray'
  if (c === 1) return 'green'
  if (c < 2) return 'red_1'
  if (c < 4) return 'red_2'
  if (c < 8) return 'red_3'
  return 'red_4'
}

export default function TreeView() {
  const stack = useStore((s) => s.stack)
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
    pushLayer({ qa_id: qa.qa_id, question, answer: '', status: 'generating', concepts: [], layer_summary: '', loading: true })
    subscribe(qa.qa_id)
    setQuestion('')
  }

  async function onDrillDown(parentQaId, conceptId, conceptName, concept) {
    if (!guardAction(null)) return
    if (concept?.understood) return
    setLastViewed(parentQaId, conceptId)
    const child = await api.drillDown(parentQaId, conceptId, conceptName)
    pushLayer({
      qa_id: child.qa_id, question: child.question || conceptName, answer: '',
      status: 'generating', concepts: [], layer_summary: '', loading: true,
    })
    api.incrementExplore(conceptId)
    subscribe(child.qa_id)
  }

  function onRollback(targetQaId) {
    popToLayer(targetQaId)
  }

  async function onCorrect(qaId, conceptId, action) {
    try {
      await api.correctAnnotation(qaId, conceptId, action)
      if (action === 'remove') {
        updateLayer(qaId, {
          concepts: getState().stack.find((l) => l.qa_id === qaId)?.concepts
            ?.filter((c) => c.concept_id !== conceptId) || []
        })
      } else {
        const name = window.prompt('输入要补抽的概念名：')
        if (!name) return
        const concepts = getState().stack.find((l) => l.qa_id === qaId)?.concepts || []
        updateLayer(qaId, {
          concepts: [...concepts, { canonical_name: name, concept_id: `local_${Date.now()}`, confidence: 1.0 }]
        })
      }
    } catch (e) {
      alert(`纠标注失败: ${e.message}`)
    }
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
          placeholder="提一个问题，开始探索…"
          onKeyDown={(e) => e.key === 'Enter' && startNewTree()}
        />
        <button style={styles.btn} onClick={startNewTree} disabled={!!inflight}>
          开新树
        </button>
      </div>

      {stack.length === 0 && (
        <div style={styles.empty}>
          <div style={styles.emptyTitle}>从一个问题开始</div>
          <div style={styles.emptyDesc}>每个问题会抽出关键概念，点击概念下钻，层层深入构建你的知识网络。</div>
        </div>
      )}

      <nav style={styles.tree}>
        {stack.map((layer, idx) => {
          const isCurrent = idx === stack.length - 1
          const nearDepthLimit = layer.depth >= MAX_DEPTH - 1
          return (
            <LayerNode
              key={layer.qa_id}
              layer={layer}
              depth={idx + 1}
              isCurrent={isCurrent}
              nearDepthLimit={nearDepthLimit}
              inflight={inflight}
              onDrillDown={onDrillDown}
              onRollback={onRollback}
              onCorrect={onCorrect}
            />
          )
        })}
      </nav>
    </aside>
  )
}

// —— 单个层节点：左侧生长茎 + 内容 ——
function LayerNode({ layer, depth, isCurrent, nearDepthLimit, inflight, onDrillDown, onRollback, onCorrect }) {
  return (
    <div style={styles.layerRow}>
      {/* 生长茎：纵向细线,当前层用活跃色,已回退层用沉淀色 */}
      <div style={{
        ...styles.stem,
        background: isCurrent ? 'var(--active)' : 'var(--rule)',
        opacity: isCurrent ? 0.7 : 1,
      }} />
      <div style={{
        ...styles.layer,
        borderLeft: isCurrent ? undefined : 'none',
      }}>
        <div style={styles.layerHead}>
          <span style={styles.depthTag}>L{depth}</span>
          <span style={styles.q}>{layer.question}</span>
          {layer.loading && <span style={styles.loadingDot} />}
        </div>

        {/* 层摘要折叠预览 */}
        {layer.layer_summary && (
          <div style={styles.layerSummary}>{layer.layer_summary}</div>
        )}

        {layer.answer && (
          <div style={styles.answer}>{layer.answer}</div>
        )}

        {layer.concepts?.length > 0 ? (
          <div style={styles.concepts}>
            {layer.concepts.map((c) => {
              const tier = tierForCount(c.explore_count || 0)
              const understood = c.understood || (c.explore_count >= 2)
              const isLastViewed = layer.last_viewed_concept === c.concept_id
              return (
                <span key={c.concept_id} style={styles.chipWrap}>
                  <button
                    style={{
                      ...styles.chip,
                      background: understood ? 'var(--settled-soft)' : TIER_COLOR[tier],
                      color: understood ? 'var(--settled)' : '#fff',
                      opacity: understood ? 0.6 : 1,
                      cursor: understood ? 'not-allowed' : 'pointer',
                      boxShadow: isLastViewed ? '0 0 0 2px var(--active)' : 'none',
                    }}
                    title={understood ? '已理解（探索≥2次未下钻）' : (isLastViewed ? '你上次在这里看的概念' : '点击下钻')}
                    onClick={() => onDrillDown(layer.qa_id, c.concept_id, c.canonical_name || c.name, c)}
                    disabled={!!inflight && inflight !== layer.qa_id}
                  >
                    {c.canonical_name || c.name}
                    {isLastViewed && ' ←'}
                  </button>
                  <button
                    style={styles.chipDel}
                    title="删除误抽的概念"
                    onClick={() => onCorrect(layer.qa_id, c.concept_id, 'remove')}
                    disabled={!!inflight && inflight !== layer.qa_id}
                  >×</button>
                </span>
              )
            })}
            <button
              style={styles.chipAdd}
              onClick={() => onCorrect(layer.qa_id, null, 'add')}
              disabled={!!inflight && inflight !== layer.qa_id}
            >+ 补抽</button>
          </div>
        ) : (
          layer.status === 'waiting' && (
            <div style={styles.noConcepts}>本轮未抽取到概念</div>
          )
        )}

        {nearDepthLimit && isCurrent && (
          <div style={styles.depthWarn}>即将到达探索深度上限（L{MAX_DEPTH}）</div>
        )}

        {!isCurrent && (
          <button style={styles.rollback} onClick={() => onRollback(layer.qa_id)}>
            ↑ 回到这层
          </button>
        )}
      </div>
    </div>
  )
}

const styles = {
  wrap: {
    width: 380, borderRight: '1px solid var(--rule)', padding: 16,
    overflowY: 'auto', background: 'var(--paper)',
  },
  inputRow: { display: 'flex', gap: 6, marginBottom: 16 },
  input: {
    flex: 1, padding: '8px 10px', border: '1px solid var(--rule)',
    borderRadius: 'var(--r-sm)', background: '#fff', fontFamily: 'var(--sans)',
    fontSize: 13, color: 'var(--ink)',
  },
  btn: {
    padding: '8px 14px', background: 'var(--active)', color: '#fff',
    border: 'none', borderRadius: 'var(--r-sm)', cursor: 'pointer',
    fontFamily: 'var(--sans)', fontSize: 13,
  },
  empty: { padding: '32px 12px', textAlign: 'center' },
  emptyTitle: {
    fontFamily: 'var(--serif)', fontSize: 16, color: 'var(--ink)', marginBottom: 8,
  },
  emptyDesc: { fontSize: 12, color: 'var(--ink-soft)', lineHeight: 1.7 },
  tree: { display: 'flex', flexDirection: 'column', gap: 4 },
  layerRow: { display: 'flex', gap: 10, position: 'relative' },
  // 生长茎：纵向细线,贯穿层节点高度
  stem: {
    width: 2, flexShrink: 0, borderRadius: 1, alignSelf: 'stretch',
    marginLeft: 4, minHeight: 40,
  },
  layer: { flex: 1, paddingBottom: 12, paddingLeft: 4, minWidth: 0 },
  layerHead: { display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 },
  depthTag: {
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--active)',
    background: 'var(--active-soft)', borderRadius: 'var(--r-sm)',
    padding: '2px 6px', flexShrink: 0,
  },
  q: {
    fontFamily: 'var(--serif)', fontSize: 14, fontWeight: 500,
    color: 'var(--ink)', lineHeight: 1.4,
  },
  loadingDot: {
    width: 6, height: 6, borderRadius: '50%', background: 'var(--active)',
    flexShrink: 0, animation: 'pulse 1.4s ease-in-out infinite', alignSelf: 'center',
  },
  layerSummary: {
    fontFamily: 'var(--serif)', fontSize: 12.5, fontStyle: 'italic',
    color: 'var(--ink-soft)', margin: '6px 0', padding: '6px 10px',
    background: 'var(--paper-soft)', borderRadius: 'var(--r-sm)',
    borderLeft: '2px solid var(--settled)', lineHeight: 1.5,
  },
  answer: {
    fontSize: 13, color: 'var(--ink)', margin: '6px 0', lineHeight: 1.65,
    whiteSpace: 'pre-wrap', fontFamily: 'var(--sans)',
  },
  concepts: { display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8, alignItems: 'center' },
  chipWrap: { display: 'inline-flex', alignItems: 'center', position: 'relative' },
  chip: {
    border: 'none', borderRadius: 'var(--r-pill)', padding: '3px 10px',
    fontSize: 12, cursor: 'pointer', fontFamily: 'var(--sans)', fontWeight: 500,
  },
  chipDel: {
    position: 'absolute', right: -4, top: -4, width: 14, height: 14,
    borderRadius: '50%', background: 'var(--danger)', color: '#fff',
    border: '1.5px solid var(--paper)', fontSize: 10, cursor: 'pointer',
    padding: 0, lineHeight: '11px', textAlign: 'center',
  },
  chipAdd: {
    border: '1px dashed var(--rule)', borderRadius: 'var(--r-pill)',
    padding: '3px 10px', fontSize: 12, cursor: 'pointer', background: 'transparent',
    color: 'var(--ink-soft)', fontFamily: 'var(--mono)',
  },
  noConcepts: { fontSize: 11, color: 'var(--ink-faint)', marginTop: 6, fontStyle: 'italic' },
  depthWarn: {
    fontSize: 11, color: 'var(--danger)', marginTop: 8, padding: '6px 10px',
    background: 'var(--danger-soft)', borderRadius: 'var(--r-sm)',
    borderLeft: '2px solid var(--danger)',
  },
  rollback: {
    marginTop: 6, fontSize: 12, color: 'var(--active)', background: 'none',
    border: 'none', cursor: 'pointer', padding: 0, fontFamily: 'var(--sans)',
  },
}
