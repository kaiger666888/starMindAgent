import React, { useMemo, useState, useRef, useEffect } from 'react'
import * as api from '../api/client'
import { useStore, updateLayer, clearInflight, setLastViewed, guardAction, goBack, findNode } from '../store/qaStore'
import { renderMarkdown } from './markdownRenderer.jsx'
import ReaderControls from './ReaderControls'

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
  const tree = useStore((s) => s.tree)
  const currentPath = useStore((s) => s.currentPath)
  const inflight = useStore((s) => s.inflight)
  const activeSid = useStore((s) => s.activeSessionId)

  // 当前层 = currentPath 末尾节点（树状分支结构）
  const current = currentPath.length > 0 ? findNode(currentPath[currentPath.length - 1]) : null

  // 键盘翻页:Alt+← 后退(沿 path 向上)
  useEffect(() => {
    const onKey = (e) => {
      if (!e.altKey) return
      if (e.key === 'ArrowLeft') { e.preventDefault(); goBack() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (!current) {
    return (
      <div style={styles.empty}>
        <div style={styles.emptyStem} aria-hidden="true" />
        <div style={styles.emptyTitle}>从一个问题开始</div>
        <div style={styles.emptyDesc}>
          在左侧提一个问题，回答会在这里展开。<br />
          回答中的关键概念会内联标注，点击可层层下钻，长成一棵探索树。
        </div>
        <div style={styles.emptyHint}>例：什么是梯度下降？ · 为什么需要激活函数？</div>
      </div>
    )
  }

  const depth = currentPath.length
  const canBack = currentPath.length > 1
  return (
    <ReadingLayer
      layer={current}
      depth={depth}
      inflight={inflight}
      canBack={canBack}
    />
  )
}

function ReadingLayer({ layer, depth, inflight, canBack }) {
  // concepts 用于:正文 markdown 渲染时做内联概念切分(可下钻) + unmatched 提示
  const concepts = layer.concepts || []
  // buildInlineSegments 只为拿 unmatched(渲染逻辑独立,匹配规则与渲染器一致)
  const { unmatched } = useMemo(
    () => buildInlineSegments(layer.answer || '', concepts),
    [layer.answer, concepts]
  )

  return (
    <div style={styles.wrap}>
      <div style={styles.scrollWrap}>
        <ReaderControls />
        {/* 层标签 + 生长茎(签名元素) */}
        <header style={styles.layerHeader}>
          <div style={styles.stem} aria-hidden="true">
            <span style={styles.stemNode} />
          </div>
          <div style={styles.layerMeta}>
            <span style={styles.depthTag}>第 {depth} 层</span>
            {canBack && (
              <div style={styles.pager} aria-label="探索历史翻页">
                <button
                  style={styles.pageBtn}
                  onClick={goBack}
                  title="上一层"
                  aria-label="上一层"
                >‹</button>
              </div>
            )}
            <h1 style={styles.layerQ}>{layer.question}</h1>
            {layer.loading && <span style={styles.loadingDot} title="生成中" />}
          </div>
        </header>

        {/* 回答正文 + 内联概念 —— markdown 渲染,衬线书本感 */}
        <article style={styles.answer}>
          {layer.answer ? (
            <InlineAnswer answer={layer.answer} concepts={concepts} layer={layer} inflight={inflight} />
          ) : (
            <div style={styles.generating}>
              {layer.loading ? '正在落笔…' : '（空回答）'}
            </div>
          )}
        </article>

        {/* 层摘要 —— 沉淀信号,陶土棕左边线 */}
        {layer.layer_summary && (
          <aside style={styles.layerSummary}>
            <span style={styles.summaryMark}>摘</span>
            <span style={styles.summaryText}>{layer.layer_summary}</span>
          </aside>
        )}

        {/* 未匹配的概念(抽取了但正文没出现) */}
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

        {/* 选中创建提示 —— 静默脚注,不抢正文 */}
        <div style={styles.hint}>选中正文里的词，可标为概念并下钻</div>
      </div>
    </div>
  )
}

// —— 内联回答:markdown 渲染正文,概念位置渲染为可点击的内联 chip ——
function InlineAnswer({ answer, concepts, layer, inflight }) {
  const [selection, setSelection] = useState(null)
  const articleRef = useRef(null)

  // 概念渲染回调:markdown 渲染器在概念位置调它,返回内联 ConceptInline
  const renderConcept = (concept) => (
    <ConceptInline concept={concept} inflight={inflight} layer={layer} inline />
  )

  function handleMouseUp() {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed) { setSelection(null); return }
    const text = sel.toString().trim()
    if (text.length < 2 || text.length > 20) { setSelection(null); return }
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
    const localId = `local_${Date.now()}`
    try {
      await api.correctAnnotation(layer.qa_id, null, 'add')
    } catch (e) { /* 后端 add 需 concept_id,这里用本地 */ }
    const child = await api.drillDown(layer.qa_id, localId, text)
    const { pushLayer } = await import('../store/qaStore')
    pushLayer({
      qa_id: child.qa_id, question: child.question || text, answer: '',
      status: 'generating', concepts: [], layer_summary: '', loading: true,
    })
    api.incrementExplore(localId)
    api.subscribeStream(child.qa_id, {
      answer_delta: (ev) => {
        const cur = findNode(child.qa_id)
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
      {renderMarkdown(answer, concepts, renderConcept)}
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
        const cur = findNode(child.qa_id)
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
    // 内联在正文里:墨蓝下划点(未探索)/陶土棕虚线(已理解),不抢正文
    const understoodStyle = understood ? {
      color: 'var(--settled)',
      textDecoration: 'underline',
      textDecorationStyle: 'dashed',
      textDecorationColor: 'var(--settled)',
      textDecorationThickness: '1px',
      textUnderlineOffset: '4px',
      cursor: 'default',
    } : {
      color: 'var(--active)',
      textDecoration: 'underline',
      textDecorationStyle: 'dotted',
      textDecorationColor: 'var(--active)',
      textDecorationThickness: '1.5px',
      textUnderlineOffset: '4px',
      cursor: 'pointer',
    }
    return (
      <span
        style={{ ...styles.inline, ...understoodStyle }}
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
  wrap: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'var(--paper)', overflow: 'auto' },
  // 阅读区:max-width 680(中文长行 40-55 字舒适),上下加大呼吸
  scrollWrap: { maxWidth: 680, margin: '0 auto', padding: '32px 36px 56px', width: '100%', minHeight: '100%', position: 'relative' },
  // 空态:加一根短茎暗示"等一个问题落下"
  empty: { flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', padding: 48, background: 'var(--paper)', gap: 14 },
  emptyStem: { width: 3, height: 28, borderRadius: 2, background: 'var(--rule)', marginBottom: 4 },
  emptyTitle: { fontFamily: 'var(--serif)', fontSize: 20, color: 'var(--ink)', fontWeight: 600, letterSpacing: '0.01em' },
  emptyDesc: { fontSize: 13.5, color: 'var(--ink-soft)', lineHeight: 1.75, textAlign: 'center', maxWidth: 380, fontFamily: 'var(--serif)' },
  emptyHint: { marginTop: 4, fontSize: 11.5, color: 'var(--ink-faint)', fontFamily: 'var(--mono)', letterSpacing: '0.04em' },
  // 层标题 + 生长茎(签名):茎 4px 宽,带呼吸光晕,顶部一颗节点
  layerHeader: { display: 'flex', gap: 14, marginBottom: 20, paddingBottom: 16, borderBottom: '1px solid var(--rule-soft)' },
  stem: {
    width: 4, flexShrink: 0, borderRadius: 2, alignSelf: 'stretch', minHeight: 36,
    background: 'linear-gradient(180deg, var(--active) 0%, rgba(43,95,138,0.25) 100%)',
    animation: 'stemBreath 2.6s ease-in-out infinite', position: 'relative',
  },
  stemNode: {
    position: 'absolute', top: -2, left: '50%', transform: 'translateX(-50%)',
    width: 8, height: 8, borderRadius: '50%', background: 'var(--active)',
    boxShadow: '0 0 0 3px var(--active-soft)',
  },
  layerMeta: { display: 'flex', alignItems: 'baseline', gap: 10, flex: 1, flexWrap: 'wrap' },
  depthTag: {
    fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--active)',
    background: 'var(--active-soft)', borderRadius: 'var(--r-sm)', padding: '2px 8px',
    letterSpacing: '0.04em', flexShrink: 0,
  },
  // 翻页器:学习手账的"翻页"隐喻——前进/后退切层,中间页码指示进度
  pager: {
    display: 'inline-flex', alignItems: 'center', gap: 0, flexShrink: 0,
    border: '1px solid var(--rule)', borderRadius: 'var(--r-sm)',
    background: 'var(--paper)', overflow: 'hidden',
  },
  pageBtn: {
    width: 22, height: 22, border: 'none', background: 'transparent',
    cursor: 'pointer', color: 'var(--active)', fontFamily: 'var(--serif)',
    fontSize: 16, lineHeight: 1, padding: 0, transition: 'background 0.15s, color 0.15s',
  },
  layerQ: {
    fontFamily: 'var(--serif)', fontSize: 18, fontWeight: 600, color: 'var(--ink)',
    lineHeight: 1.4, flex: 1, margin: 0, letterSpacing: '0.005em',
  },
  loadingDot: {
    width: 6, height: 6, borderRadius: '50%', background: 'var(--active)',
    animation: 'pulse 1.4s ease-in-out infinite', alignSelf: 'center', flexShrink: 0,
  },
  // 正文:衬线 15px / 行高 1.9 / 字距 0.01em —— 书本感
  answer: {
    fontSize: 'var(--fs-body)', lineHeight: 'var(--lh-body)', color: 'var(--ink-read)',
    fontFamily: 'var(--serif)', letterSpacing: 'var(--tracking-body)',
    textRendering: 'optimizeLegibility', WebkitFontSmoothing: 'antialiased',
  },
  articleInner: { position: 'relative', wordBreak: 'break-word' },
  generating: { color: 'var(--ink-soft)', fontStyle: 'italic', fontFamily: 'var(--serif)', fontSize: 'var(--fs-body)' },
  inline: {
    fontWeight: 500, position: 'relative',
    transition: 'color 0.15s, text-decoration-color 0.15s',
  },
  tooltip: {
    position: 'absolute', bottom: '100%', left: '50%', transform: 'translateX(-50%)',
    background: 'var(--ink)', color: 'var(--paper)', fontSize: 10,
    padding: '3px 8px', borderRadius: 'var(--r-sm)', whiteSpace: 'nowrap',
    fontFamily: 'var(--mono)', marginBottom: 6, pointerEvents: 'none',
    boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
  },
  // 层摘要:陶土棕左边线 + "摘"字标记,衬线斜体,视觉层级介于正文与脚注间
  layerSummary: {
    display: 'flex', gap: 10, alignItems: 'flex-start',
    fontFamily: 'var(--serif)', fontSize: 13.5, fontStyle: 'italic',
    color: 'var(--ink-soft)', margin: '24px 0 0', padding: '12px 16px',
    background: 'var(--paper-warm)', borderRadius: 'var(--r-md)',
    borderLeft: '3px solid var(--settled)', lineHeight: 1.7,
  },
  summaryMark: {
    fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--settled)',
    background: 'var(--settled-soft)', borderRadius: 'var(--r-sm)',
    padding: '0 6px', flexShrink: 0, lineHeight: 1.9, fontStyle: 'normal',
  },
  summaryText: { flex: 1 },
  // 未匹配概念块
  unmatchedBlock: { marginTop: 20, padding: '10px 14px', background: 'var(--paper-soft)', borderRadius: 'var(--r-md)' },
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
    marginTop: 28, fontSize: 11, color: 'var(--ink-faint)', textAlign: 'center',
    fontFamily: 'var(--mono)', letterSpacing: '0.04em',
  },
}
