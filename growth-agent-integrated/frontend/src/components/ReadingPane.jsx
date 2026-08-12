import React, { useMemo, useState, useRef, useEffect } from 'react'
import * as api from '../api/client'
import { useStore, updateLayer, clearInflight, getState, setLastViewed, guardAction } from '../store/qaStore'

// 阅读主区：当前层的回答正文 + 内联概念 + 层摘要 + 正文选中创建概念
// 签名元素：层标签左侧的"生长茎"——depth 越深茎越长，跨层连续生长感

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

export default function ReadingPane() {
  const stack = useStore((s) => s.stack)
  const inflight = useStore((s) => s.inflight)
  const activeSid = useStore((s) => s.activeSessionId)

  // 当前层 = 栈顶
  const current = stack[stack.length - 1]

  if (!current) {
    return (
      <div style={styles.empty}>
        <div style={styles.emptyTitle}>从一个问题开始</div>
        <div style={styles.emptyDesc}>在左侧输入问题，回答会在这里展开。<br />回答中的关键概念会内联标注，点击下钻层层深入。</div>
      </div>
    )
  }

  return <ReadingLayer layer={current} depth={stack.length} inflight={inflight} />
}

function ReadingLayer({ layer, depth, inflight }) {
  // 内联概念：把 concepts 按 canonical_name/aliases 在正文里找首次出现位置
  const { segments, unmatched } = useMemo(
    () => buildInlineSegments(layer.answer || '', layer.concepts || []),
    [layer.answer, layer.concepts]
  )

  return (
    <div style={styles.wrap}>
      <div style={styles.scrollWrap}>
        {/* 层标签 + 生长茎 */}
        <div style={styles.layerHeader}>
          <div style={styles.stem} />
          <div style={styles.layerMeta}>
            <span style={styles.depthTag}>L{depth}</span>
            <span style={styles.layerQ}>{layer.question}</span>
            {layer.loading && <span style={styles.loadingDot} />}
          </div>
        </div>

        {/* 回答正文 + 内联概念 */}
        <article style={styles.answer}>
          {layer.answer ? (
            <InlineAnswer segments={segments} layer={layer} inflight={inflight} />
          ) : (
            <div style={styles.generating}>{layer.loading ? '正在生成回答…' : '（空回答）'}</div>
          )}
        </article>

        {/* 层摘要 */}
        {layer.layer_summary && (
          <div style={styles.layerSummary}>{layer.layer_summary}</div>
        )}

        {/* 未匹配的概念（抽取了但正文没出现） */}
        {unmatched.length > 0 && (
          <div style={styles.unmatchedBlock}>
            <div style={styles.unmatchedLabel}>抽取但正文未出现</div>
            <div style={styles.unmatchedChips}>
              {unmatched.map((c) => (
                <ConceptInline key={c.concept_id} concept={c} inflight={inflight} layer={layer} />
              ))}
            </div>
          </div>
        )}

        {/* 选中创建提示 */}
        <div style={styles.hint}>选中正文里的词，可标为概念并下钻</div>
      </div>
    </div>
  )
}

// —— 内联回答：把正文按概念位置切分，概念位置渲染为可点击的内联 chip ——
function InlineAnswer({ segments, layer, inflight }) {
  const [selection, setSelection] = useState(null)
  const articleRef = useRef(null)

  function handleMouseUp() {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed) { setSelection(null); return }
    const text = sel.toString().trim()
    if (text.length < 2 || text.length > 20) { setSelection(null); return }
    // 获取选中位置
    const range = sel.getRangeAt(0)
    const rect = range.getBoundingClientRect()
    const articleRect = articleRef.current?.getBoundingClientRect()
    if (!articleRect) return
    setSelection({
      text,
      x: rect.left - articleRect.left + rect.width / 2,
      y: rect.top - articleRect.top,
    })
  }

  async function onCreateAndDrill(text) {
    if (!guardAction(null)) return
    setLastViewed(layer.qa_id, null)
    // 1. 调 correct API add 把选中词标为概念（新建本地概念）
    const localId = `local_${Date.now()}`
    try {
      await api.correctAnnotation(layer.qa_id, null, 'add')
    } catch (e) { /* 后端 add 需 concept_id,这里用本地 */ }
    // 2. 直接以下钻方式开新层（用选中词作 question）
    const child = await api.drillDown(layer.qa_id, localId, text)
    // 本地 pushLayer
    const { pushLayer } = await import('../store/qaStore')
    pushLayer({
      qa_id: child.qa_id, question: child.question || text, answer: '',
      status: 'generating', concepts: [], layer_summary: '', loading: true,
    })
    api.incrementExplore(localId)
    // 订阅 SSE
    api.subscribeStream(child.qa_id, {
      answer_delta: (ev) => {
        const cur = getState().stack.find((l) => l.qa_id === child.qa_id)
        updateLayer(child.qa_id, { answer: (cur?.answer || '') + ev.text })
      },
      status: (ev) => updateLayer(child.qa_id, { status: ev.status }),
      concepts: (ev) => updateLayer(child.qa_id, { concepts: ev.concepts }),
      layer_summary: (ev) => updateLayer(child.qa_id, { layer_summary: ev.layer_summary }),
      done: () => { updateLayer(child.qa_id, { loading: false }); clearInflight() },
      error: () => { updateLayer(child.qa_id, { loading: false }); clearInflight() },
    })
    setSelection(null)
    window.getSelection()?.removeAllRanges()
  }

  return (
    <div ref={articleRef} style={styles.articleInner} onMouseUp={handleMouseUp}>
      {segments.map((seg, i) => (
        seg.type === 'text' ? (
          <React.Fragment key={i}>{seg.text}</React.Fragment>
        ) : (
          <ConceptInline key={i} concept={seg.concept} inflight={inflight} layer={layer} inline />
        )
      ))}
      {selection && (
        <div style={{ ...styles.popover, left: selection.x, top: selection.y }}>
          <button style={styles.popoverBtn} onClick={() => onCreateAndDrill(selection.text)}>
            标为概念·下钻
          </button>
        </div>
      )}
    </div>
  )
}

// —— 内联概念 chip（可点击下钻）——
function ConceptInline({ concept, inflight, layer, inline }) {
  const [showTip, setShowTip] = useState(false)
  const understood = concept.understood || (concept.explore_count >= 2)
  const tier = tierForCount(concept.explore_count || 0)
  const name = concept.canonical_name || concept.name

  async function onDrill() {
    if (!guardAction(null) || understood) return
    setLastViewed(layer.qa_id, concept.concept_id)
    const child = await api.drillDown(layer.qa_id, concept.concept_id, name)
    const { pushLayer } = await import('../store/qaStore')
    pushLayer({
      qa_id: child.qa_id, question: child.question || name, answer: '',
      status: 'generating', concepts: [], layer_summary: '', loading: true,
    })
    api.incrementExplore(concept.concept_id)
    api.subscribeStream(child.qa_id, {
      answer_delta: (ev) => {
        const cur = getState().stack.find((l) => l.qa_id === child.qa_id)
        updateLayer(child.qa_id, { answer: (cur?.answer || '') + ev.text })
      },
      status: (ev) => updateLayer(child.qa_id, { status: ev.status }),
      concepts: (ev) => updateLayer(child.qa_id, { concepts: ev.concepts }),
      layer_summary: (ev) => updateLayer(child.qa_id, { layer_summary: ev.layer_summary }),
      done: () => { updateLayer(child.qa_id, { loading: false }); clearInflight() },
      error: () => { updateLayer(child.qa_id, { loading: false }); clearInflight() },
    })
  }

  if (inline) {
    // 内联在正文里：加下划线 + 着色文字
    return (
      <span
        style={{
          ...styles.inline,
          color: understood ? 'var(--settled)' : TIER_COLOR[tier],
          cursor: understood ? 'default' : 'pointer',
          textDecoration: understood ? 'none' : 'underline',
          textDecorationStyle: 'dotted',
          textDecorationThickness: '2px',
          textUnderlineOffset: '3px',
        }}
        onMouseEnter={() => setShowTip(true)}
        onMouseLeave={() => setShowTip(false)}
        onClick={onDrill}
        title={understood ? '已理解' : '点击下钻'}
      >
        {name}
        {showTip && (
          <span style={styles.tooltip}>
            {understood ? '已理解' : `点击下钻 · 置信度 ${(concept.confidence * 100).toFixed(0)}%`}
          </span>
        )}
      </span>
    )
  }
  // 非内联（unmatched 块）：pill 样式
  return (
    <button
      style={{
        ...styles.chip,
        background: understood ? 'var(--settled-soft)' : TIER_COLOR[tier],
        color: understood ? 'var(--settled)' : '#fff',
        opacity: understood ? 0.6 : 1,
        cursor: understood ? 'not-allowed' : 'pointer',
      }}
      onClick={onDrill}
      disabled={understood}
    >
      {name}
    </button>
  )
}

// —— 把正文按概念首次出现位置切分成 segments ——
function buildInlineSegments(answer, concepts) {
  if (!answer) return { segments: [{ type: 'text', text: '' }], unmatched: [] }
  // 收集每个概念的匹配位置（canonical_name + aliases，取最早出现）
  const matches = []
  for (const c of concepts) {
    const names = [c.canonical_name, ...(c.aliases || [])].filter(Boolean)
    let earliest = -1, matchedName = null
    for (const n of names) {
      const idx = answer.indexOf(n)
      if (idx >= 0 && (earliest < 0 || idx < earliest)) {
        earliest = idx
        matchedName = n
      }
    }
    if (earliest >= 0) {
      matches.push({ concept: c, start: earliest, end: earliest + matchedName.length })
    }
  }
  // 按位置排序，去重叠（保留最早出现的概念）
  matches.sort((a, b) => a.start - b.start)
  const valid = []
  let lastEnd = 0
  for (const m of matches) {
    if (m.start >= lastEnd) {
      valid.push(m)
      lastEnd = m.end
    }
  }
  // 切分
  const segments = []
  let pos = 0
  for (const m of valid) {
    if (m.start > pos) segments.push({ type: 'text', text: answer.slice(pos, m.start) })
    segments.push({ type: 'concept', concept: m.concept })
    pos = m.end
  }
  if (pos < answer.length) segments.push({ type: 'text', text: answer.slice(pos) })
  // 未匹配的概念
  const matchedIds = new Set(valid.map((m) => m.concept.concept_id))
  const unmatched = concepts.filter((c) => !matchedIds.has(c.concept_id))
  return { segments, unmatched }
}

const styles = {
  wrap: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'var(--paper)' },
  scrollWrap: { maxWidth: 720, margin: '0 auto', padding: '24px 32px 48px', width: '100%' },
  empty: { flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', padding: 48, background: 'var(--paper)' },
  emptyTitle: { fontFamily: 'var(--serif)', fontSize: 18, color: 'var(--ink)', marginBottom: 12 },
  emptyDesc: { fontSize: 13, color: 'var(--ink-soft)', lineHeight: 1.7, textAlign: 'center', maxWidth: 400 },
  // 层标签 + 生长茎
  layerHeader: { display: 'flex', gap: 12, marginBottom: 16, paddingBottom: 12, borderBottom: '1px solid var(--rule-soft)' },
  stem: {
    width: 3, flexShrink: 0, borderRadius: 2, alignSelf: 'stretch', minHeight: 32,
    background: 'var(--active)', animation: 'stemBreath 2.5s ease-in-out infinite',
  },
  layerMeta: { display: 'flex', alignItems: 'baseline', gap: 10, flex: 1, flexWrap: 'wrap' },
  depthTag: {
    fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--active)',
    background: 'var(--active-soft)', borderRadius: 'var(--r-sm)', padding: '2px 8px',
  },
  layerQ: {
    fontFamily: 'var(--serif)', fontSize: 17, fontWeight: 600, color: 'var(--ink)',
    lineHeight: 1.3, flex: 1,
  },
  loadingDot: {
    width: 6, height: 6, borderRadius: '50%', background: 'var(--active)',
    animation: 'pulse 1.4s ease-in-out infinite', alignSelf: 'center',
  },
  // 回答正文
  answer: { fontSize: 14.5, lineHeight: 1.85, color: 'var(--ink)', fontFamily: 'var(--sans)' },
  articleInner: { whiteSpace: 'pre-wrap', position: 'relative' },
  generating: { color: 'var(--ink-soft)', fontStyle: 'italic' },
  inline: {
    cursor: 'pointer', fontWeight: 500, position: 'relative',
    transition: 'color 0.15s',
  },
  tooltip: {
    position: 'absolute', bottom: '100%', left: '50%', transform: 'translateX(-50%)',
    background: 'var(--ink)', color: 'var(--paper)', fontSize: 10,
    padding: '3px 8px', borderRadius: 'var(--r-sm)', whiteSpace: 'nowrap',
    fontFamily: 'var(--mono)', marginBottom: 4, pointerEvents: 'none',
  },
  // 层摘要
  layerSummary: {
    fontFamily: 'var(--serif)', fontSize: 13, fontStyle: 'italic',
    color: 'var(--ink-soft)', margin: '20px 0 0', padding: '10px 14px',
    background: 'var(--paper-soft)', borderRadius: 'var(--r-sm)',
    borderLeft: '2px solid var(--settled)', lineHeight: 1.6,
  },
  // 未匹配概念块
  unmatchedBlock: { marginTop: 20, padding: '10px 14px', background: 'var(--paper-soft)', borderRadius: 'var(--r-sm)' },
  unmatchedLabel: {
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-faint)',
    textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8,
  },
  unmatchedChips: { display: 'flex', flexWrap: 'wrap', gap: 6 },
  chip: {
    border: 'none', borderRadius: 'var(--r-pill)', padding: '3px 10px',
    fontSize: 12, fontFamily: 'var(--sans)', fontWeight: 500,
  },
  // 选中创建气泡
  popover: {
    position: 'absolute', transform: 'translate(-50%, -100%)', zIndex: 10,
    background: 'var(--ink)', borderRadius: 'var(--r-sm)', padding: 2,
    boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
  },
  popoverBtn: {
    background: 'var(--ink)', color: 'var(--paper)', border: 'none',
    padding: '6px 12px', fontSize: 12, cursor: 'pointer',
    borderRadius: 'var(--r-sm)', fontFamily: 'var(--sans)', fontWeight: 500,
  },
  hint: {
    marginTop: 24, fontSize: 11, color: 'var(--ink-faint)', textAlign: 'center',
    fontFamily: 'var(--mono)', letterSpacing: '0.04em',
  },
}
