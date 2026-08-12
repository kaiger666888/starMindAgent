import React, { useState } from 'react'
import * as api from '../api/client'
import {
  useStore, pushLayer, updateLayer, popToLayer, setLastViewed,
  clearInflight, guardAction, getState, setActiveSession,
} from '../store/qaStore'

const MAX_DEPTH = 6

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

      {stack.length === 0 && (
        <div style={styles.empty}>提一个问题开始探索</div>
      )}

      <nav style={styles.tree}>
        {stack.map((layer, idx) => {
          const isCurrent = idx === stack.length - 1
          return (
            <button
              key={layer.qa_id}
              style={{
                ...styles.layerBtn,
                borderLeft: isCurrent ? '3px solid var(--active)' : '3px solid transparent',
                background: isCurrent ? 'var(--active-soft)' : 'transparent',
              }}
              onClick={() => popToLayer(layer.qa_id)}
            >
              <div style={styles.layerHead}>
                <span style={styles.depthTag}>L{idx + 1}</span>
                <span style={styles.q}>{layer.question}</span>
                {layer.loading && <span style={styles.loadingDot} />}
              </div>
              {layer.layer_summary && (
                <div style={styles.preview}>{layer.layer_summary}</div>
              )}
            </button>
          )
        })}
      </nav>
    </aside>
  )
}

const styles = {
  wrap: { width: 280, borderRight: '1px solid var(--rule)', padding: 12, overflowY: 'auto', background: 'var(--paper-soft)', flexShrink: 0 },
  inputRow: { display: 'flex', gap: 6, marginBottom: 16 },
  input: { flex: 1, padding: '7px 10px', border: '1px solid var(--rule)', borderRadius: 'var(--r-sm)', background: 'var(--paper)', fontFamily: 'var(--sans)', fontSize: 12, color: 'var(--ink)' },
  btn: { padding: '7px 12px', background: 'var(--active)', color: '#fff', border: 'none', borderRadius: 'var(--r-sm)', cursor: 'pointer', fontFamily: 'var(--sans)', fontSize: 12 },
  empty: { fontSize: 11, color: 'var(--ink-faint)', padding: '16px 8px', textAlign: 'center', fontFamily: 'var(--mono)' },
  tree: { display: 'flex', flexDirection: 'column', gap: 2 },
  layerBtn: { textAlign: 'left', background: 'transparent', border: 'none', borderRadius: 'var(--r-sm)', padding: '8px 10px', cursor: 'pointer', fontFamily: 'var(--sans)', color: 'var(--ink)' },
  layerHead: { display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 },
  depthTag: { fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--active)', background: 'var(--paper)', borderRadius: 'var(--r-sm)', padding: '1px 5px' },
  q: { fontFamily: 'var(--serif)', fontSize: 12, fontWeight: 500, color: 'var(--ink)', lineHeight: 1.3, flex: 1, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' },
  loadingDot: { width: 5, height: 5, borderRadius: '50%', background: 'var(--active)', animation: 'pulse 1.4s ease-in-out infinite', flexShrink: 0, alignSelf: 'center' },
  preview: { fontSize: 10, color: 'var(--ink-soft)', fontStyle: 'italic', lineHeight: 1.4, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' },
}
