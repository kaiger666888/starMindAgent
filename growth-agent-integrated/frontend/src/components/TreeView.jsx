import React, { useState } from 'react'
import * as api from '../api/client'
import {
  useStore, pushLayer, updateLayer, popToLayer, setLastViewed,
  clearInflight, guardAction, getState, setActiveSession,
} from '../store/qaStore'

// 三状态着色：热度 -> 颜色（后端 _color_tier 对应）
const TIER_COLOR = {
  gray: '#9ca3af', green: '#22c55e',
  red_1: '#fca5a5', red_2: '#f87171', red_3: '#ef4444', red_4: '#b91c1c',
}
const MAX_DEPTH = 6  // 膨胀控制硬上限

export default function TreeView() {
  const stack = useStore((s) => s.stack)
  const inflight = useStore((s) => s.inflight)
  const [question, setQuestion] = useState('')

  // 状态三导航：点灰色未探索概念 → 预填为问题
  React.useEffect(() => {
    const handler = (e) => setQuestion(e.detail)
    window.addEventListener('starmind:prefillQuestion', handler)
    return () => window.removeEventListener('starmind:prefillQuestion', handler)
  }, [])

  // 出口3：新问题 -> 开新探索树
  async function startNewTree() {
    if (!question.trim()) return
    const uid = localStorage.getItem('starMindAgent.uid') || 'default'
    const qa = await api.startQA(question, null, null, uid)
    setActiveSession(qa.session_id)
    pushLayer({ qa_id: qa.qa_id, question, answer: '', status: 'generating', concepts: [], layer_summary: '', loading: true })
    subscribe(qa.qa_id)
    setQuestion('')
  }

  // 出口1：点击概念下钻 -> fork 新 QAStep，挂 parent_qa_id
  async function onDrillDown(parentQaId, conceptId, conceptName, concept) {
    if (!guardAction(null)) return // 在途互斥
    // 概念成熟度信号（需求三）：explore≥2 且 understood -> 入口变灰，不响应
    if (concept?.understood) return
    // 记录"上次在这里看的概念"（需求二节：回上层高亮探索断点）
    setLastViewed(parentQaId, conceptId)
    const child = await api.drillDown(parentQaId, conceptId, conceptName)
    pushLayer({
      qa_id: child.qa_id, question: conceptName, answer: '',
      status: 'generating', concepts: [], layer_summary: '', loading: true,
    })
    api.incrementExplore(conceptId) // 热度 +1
    subscribe(child.qa_id)
  }

  // 出口2：回上层（栈式回退，状态保留）
  function onRollback(targetQaId) {
    popToLayer(targetQaId)
  }

  // 手动纠标注（需求六"手动纠标注入口"）
  async function onCorrect(qaId, conceptId, action) {
    try {
      await api.correctAnnotation(qaId, conceptId, action)
      // 本地更新：add/remove 概念列表
      if (action === 'remove') {
        updateLayer(qaId, {
          concepts: getState().stack.find((l) => l.qa_id === qaId)?.concepts
            ?.filter((c) => c.concept_id !== conceptId) || []
        })
      } else {
        // add: 用户输入概念名（简单实现，实际可弹选择器）
        const name = window.prompt('输入要补抽的概念名：')
        if (!name) return
        // 补抽的概念无 concept_id（新建），本地加占位
        const concepts = getState().stack.find((l) => l.qa_id === qaId)?.concepts || []
        updateLayer(qaId, {
          concepts: [...concepts, { canonical_name: name, concept_id: `local_${Date.now()}`, confidence: 1.0 }]
        })
      }
    } catch (e) {
      alert(`纠标注失败: ${e.message}`)
    }
  }

  // SSE 流式订阅
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
          placeholder="输入新问题，开启探索树…"
          onKeyDown={(e) => e.key === 'Enter' && startNewTree()}
        />
        <button style={styles.btn} onClick={startNewTree} disabled={!!inflight}>
          开新树
        </button>
      </div>

      {stack.length === 0 && (
        <div style={styles.empty}>输入一个问题，开始你的概念探索之旅。</div>
      )}

      <nav style={styles.tree}>
        {stack.map((layer, idx) => {
          const isCurrent = idx === stack.length - 1
          const nearDepthLimit = layer.depth >= MAX_DEPTH - 1
          return (
            <div key={layer.qa_id} style={{
              ...styles.layer,
              borderLeft: isCurrent ? '3px solid #2563eb' : '3px solid transparent',
            }}>
              <div style={styles.layerHead}>
                <span style={styles.depth}>L{idx + 1}</span>
                <span style={styles.q}>{layer.question}</span>
                {layer.loading && <span style={styles.loading}>●</span>}
              </div>

              {/* 层摘要折叠预览（需求二节"总结一层"） */}
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
                            background: understood ? '#e5e7eb' : TIER_COLOR[tier],
                            opacity: understood ? 0.5 : 1,
                            cursor: understood ? 'not-allowed' : 'pointer',
                            boxShadow: isLastViewed ? '0 0 0 2px #f59e0b' : 'none',
                          }}
                          title={understood ? '已理解（探索≥2次未下钻）' : (isLastViewed ? '你上次在这里看的概念' : '点击下钻')}
                          onClick={() => onDrillDown(layer.qa_id, c.concept_id, c.canonical_name || c.name, c)}
                          disabled={!!inflight && inflight !== layer.qa_id}
                        >
                          {c.canonical_name || c.name}
                          {isLastViewed && ' ←'}
                        </button>
                        {/* 纠标注：删除误抽（需求六） */}
                        <button
                          style={styles.chipDel}
                          title="删除误抽的概念"
                          onClick={() => onCorrect(layer.qa_id, c.concept_id, 'remove')}
                          disabled={!!inflight && inflight !== layer.qa_id}
                        >×</button>
                      </span>
                    )
                  })}
                  {/* 纠标注：补抽漏抽（需求六） */}
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

              {/* 深度上限提示（需求三节） */}
              {nearDepthLimit && isCurrent && (
                <div style={styles.depthWarn}>⚠ 即将到达探索深度上限（L{MAX_DEPTH}）</div>
              )}

              {idx < stack.length - 1 && (
                <button style={styles.rollback} onClick={() => onRollback(layer.qa_id)}>
                  ↑ 回到这层
                </button>
              )}
            </div>
          )
        })}
      </nav>
    </aside>
  )
}

function tierForCount(c) {
  if (c <= 0) return 'gray'
  if (c === 1) return 'green'
  if (c < 2) return 'red_1'
  if (c < 4) return 'red_2'
  if (c < 8) return 'red_3'
  return 'red_4'
}

const styles = {
  wrap: { width: 360, borderRight: '1px solid #e5e7eb', padding: 12, overflowY: 'auto', background: '#fff' },
  inputRow: { display: 'flex', gap: 6, marginBottom: 12 },
  input: { flex: 1, padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 6 },
  btn: { padding: '6px 12px', background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer' },
  tree: { display: 'flex', flexDirection: 'column', gap: 8 },
  empty: { fontSize: 13, color: '#9ca3af', padding: '24px 8px', textAlign: 'center', lineHeight: 1.6 },
  layer: { paddingLeft: 8, paddingBottom: 8 },
  layerHead: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 },
  depth: { background: '#e0e7ff', color: '#3730a3', borderRadius: 4, padding: '0 4px', fontSize: 11 },
  q: { fontWeight: 600 },
  loading: { color: '#f59e0b', animation: 'blink 1s infinite' },
  layerSummary: {
    fontSize: 12, color: '#6b7280', fontStyle: 'italic',
    margin: '4px 0', padding: '4px 8px', background: '#f9fafb',
    borderRadius: 4, borderLeft: '2px solid #d1d5db',
  },
  answer: { fontSize: 13, color: '#374151', margin: '4px 0', lineHeight: 1.5, whiteSpace: 'pre-wrap' },
  concepts: { display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 },
  chipWrap: { display: 'inline-flex', alignItems: 'center', position: 'relative' },
  chip: { border: '1px solid #e5e7eb', borderRadius: 12, padding: '2px 8px', fontSize: 12, cursor: 'pointer', color: '#fff' },
  chipDel: { position: 'absolute', right: -2, top: -4, width: 16, height: 16, borderRadius: '50%', background: '#ef4444', color: '#fff', border: '1px solid #fff', fontSize: 11, cursor: 'pointer', padding: 0, lineHeight: '14px', textAlign: 'center' },
  chipAdd: { border: '1px dashed #9ca3af', borderRadius: 12, padding: '2px 8px', fontSize: 12, cursor: 'pointer', background: 'transparent', color: '#6b7280' },
  noConcepts: { fontSize: 11, color: '#9ca3af', marginTop: 4, fontStyle: 'italic' },
  depthWarn: { fontSize: 11, color: '#dc2626', marginTop: 4, padding: '4px 8px', background: '#fef2f2', borderRadius: 4 },
  rollback: { marginTop: 4, fontSize: 12, color: '#2563eb', background: 'none', border: 'none', cursor: 'pointer', padding: 0 },
}
