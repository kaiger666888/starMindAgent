import React, { useState } from 'react'
import * as api from '../api/client'
import {
  useStore, pushLayer, updateLayer, popToLayer,
  clearInflight, guardAction, getState, setActiveSession,
} from '../store/qaStore'

// 三状态着色：热度 -> 颜色（后端 _color_tier 对应）
const TIER_COLOR = {
  gray: '#9ca3af', green: '#22c55e',
  red_1: '#fca5a5', red_2: '#f87171', red_3: '#ef4444', red_4: '#b91c1c',
}

export default function TreeView() {
  const stack = useStore((s) => s.stack)
  const inflight = useStore((s) => s.inflight)
  const [question, setQuestion] = useState('')

  // 出口3：新问题 -> 开新探索树
  async function startNewTree() {
    if (!question.trim()) return
    const qa = await api.startQA(question, null, null)
    setActiveSession(qa.session_id)
    pushLayer({ qa_id: qa.qa_id, question, answer: '', status: 'generating', concepts: [], loading: true })
    subscribe(qa.qa_id)
    setQuestion('')
  }

  // 出口1：点击概念下钻 -> fork 新 QAStep，挂 parent_qa_id
  async function onDrillDown(parentQaId, conceptId, conceptName) {
    if (!guardAction(null)) return // 在途互斥
    const child = await api.drillDown(parentQaId, conceptId, conceptName)
    pushLayer({
      qa_id: child.qa_id, question: conceptName, answer: '',
      status: 'generating', concepts: [], loading: true,
    })
    api.incrementExplore(conceptId) // 热度 +1
    subscribe(child.qa_id)
  }

  // 出口2：回上层（栈式回退，状态保留）
  function onRollback(targetQaId) {
    popToLayer(targetQaId)
  }

  // SSE 流式订阅
  function subscribe(qaId) {
    api.subscribeStream(qaId, {
      answer_delta: (ev) => {
        // 从 store 取最新 answer，避免闭包 stale
        const cur = getState().stack.find((l) => l.qa_id === qaId)
        updateLayer(qaId, { answer: (cur?.answer || '') + ev.text })
      },
      status: (ev) => updateLayer(qaId, { status: ev.status }),
      concepts: (ev) => updateLayer(qaId, { concepts: ev.concepts }),
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

      <nav style={styles.tree}>
        {stack.map((layer, idx) => (
          <div key={layer.qa_id} style={{
            ...styles.layer,
            borderLeft: idx === stack.length - 1 ? '3px solid #2563eb' : '3px solid transparent',
          }}>
            <div style={styles.layerHead}>
              <span style={styles.depth}>L{idx + 1}</span>
              <span style={styles.q}>{layer.question}</span>
              {layer.loading && <span style={styles.loading}>●</span>}
            </div>

            {layer.answer && (
              <div style={styles.answer}>{layer.answer}</div>
            )}

            {layer.concepts?.length > 0 && (
              <div style={styles.concepts}>
                {layer.concepts.map((c) => (
                  <button
                    key={c.concept_id}
                    style={{ ...styles.chip, background: TIER_COLOR.gray }}
                    onClick={() => onDrillDown(layer.qa_id, c.concept_id, c.canonical_name || c.name)}
                    disabled={!!inflight && inflight !== layer.qa_id}
                  >
                    {c.canonical_name || c.name}
                  </button>
                ))}
              </div>
            )}

            {idx < stack.length - 1 && (
              <button style={styles.rollback} onClick={() => onRollback(layer.qa_id)}>
                ↑ 回到这层
              </button>
            )}
          </div>
        ))}
      </nav>
    </aside>
  )
}

const styles = {
  wrap: { width: 360, borderRight: '1px solid #e5e7eb', padding: 12, overflowY: 'auto', background: '#fff' },
  inputRow: { display: 'flex', gap: 6, marginBottom: 12 },
  input: { flex: 1, padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 6 },
  btn: { padding: '6px 12px', background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer' },
  tree: { display: 'flex', flexDirection: 'column', gap: 8 },
  layer: { paddingLeft: 8, paddingBottom: 8 },
  layerHead: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 },
  depth: { background: '#e0e7ff', color: '#3730a3', borderRadius: 4, padding: '0 4px', fontSize: 11 },
  q: { fontWeight: 600 },
  loading: { color: '#f59e0b', animation: 'blink 1s infinite' },
  answer: { fontSize: 13, color: '#374151', margin: '4px 0', lineHeight: 1.5, whiteSpace: 'pre-wrap' },
  concepts: { display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 },
  chip: { border: '1px solid #e5e7eb', borderRadius: 12, padding: '2px 8px', fontSize: 12, cursor: 'pointer', color: '#fff' },
  rollback: { marginTop: 4, fontSize: 12, color: '#2563eb', background: 'none', border: 'none', cursor: 'pointer', padding: 0 },
}
