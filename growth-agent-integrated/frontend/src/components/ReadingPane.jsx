import React, { useMemo, useState, useRef, useEffect } from 'react'
import * as api from '../api/client'
import { useStore, updateLayer, clearInflight, setLastViewed, guardAction, goBack, goForward, findNode, getPathNodes, getState } from '../store/qaStore'
import { renderMarkdown } from './markdownRenderer.jsx'
import ReaderControls from './ReaderControls'
import AskDialog from './AskDialog'

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
  // 订阅历史指针,派生前进/后退可用性(historyIdx 变化时组件重渲染)
  const historyIdx = useStore((s) => s.historyIdx)
  const historyLen = useStore((s) => s.history.length)
  const canBack = historyIdx > 0
  const canForward = historyIdx < historyLen - 1

  // 当前层 = currentPath 末尾节点（树状分支结构）
  const current = currentPath.length > 0 ? findNode(currentPath[currentPath.length - 1]) : null

  // 键盘翻页:Alt+← 后退 / Alt+→ 前进(在浏览历史里移动)
  useEffect(() => {
    const onKey = (e) => {
      if (!e.altKey) return
      if (e.key === 'ArrowLeft') { e.preventDefault(); goBack() }
      else if (e.key === 'ArrowRight') { e.preventDefault(); goForward() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (!current) {
    return (
      <div style={styles.empty}>
        <div style={styles.emptyTools}><ReaderControls /></div>
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
  return (
    <ReadingLayer
      layer={current}
      depth={depth}
      inflight={inflight}
      canBack={canBack}
      canForward={canForward}
      historyIdx={historyIdx}
      historyLen={historyLen}
    />
  )
}

function ReadingLayer({ layer, depth, inflight, canBack, canForward, historyIdx, historyLen }) {
  // concepts 用于:正文 markdown 渲染时做内联概念切分(可下钻) + unmatched 提示
  // 内联概念保留全部(正文自然出现的，是本层"自选概念"，和上一层展示一致)；
  // 跨层去重只对 unmatched 块生效(抽取了但正文没出现的，避免跨层重复罗列)
  //
  // 流式动态高亮(三层来源，权威优先):
  //   1. 权威列表 layer.concepts(尾部 ConceptBlock，整层生成完才到)
  //   2. 种子词:question 里的「X」引号词+英文术语(流式第 0 秒就有--
  //      用户点名的概念，正文出现即刻高亮)
  //   3. 增量候选 layer.candidates(后端 answer_delta 流上 jieba 抽取,
  //      SSE concept_candidates 事件推来) + 树上已有概念(根层导入+历层抽取)
  // 权威列表到达后只切权威(避免候选噪声盖过 LLM 精判)。
  const seedTerms = useMemo(() => extractSeedTerms(layer.question), [layer.question])
  const treeConcepts = useMemo(() => {
    const seen = new Set()
    const acc = []
    for (const n of getPathNodes()) {
      for (const c of (n.concepts || [])) {
        if (c.concept_id && !seen.has(c.concept_id)) {
          seen.add(c.concept_id)
          acc.push(c)
        }
      }
    }
    return acc
  }, [currentPathKey(layer.qa_id)])
  const ownConcepts = layer.concepts || []
  // 候选/种子词统一映射为伪概念对象参与切分(与权威概念同结构,
  // ConceptInline 按有无 concept_id 区分下钻链路)
  const candidates = useMemo(() => {
    const items = []
    const seen = new Set()
    const push = (name) => {
      if (!name || seen.has(name)) return
      seen.add(name)
      items.push({ name, canonical_name: name, aliases: [], confidence: 0.5, candidate: true })
    }
    for (const c of seedTerms) push(c)
    for (const c of (layer.candidates || [])) push(c.name)
    return items
  }, [seedTerms, layer.candidates])
  // 权威到达后不完全替换候选：候选按名称与权威合并去重，
  // 未被权威覆盖的候选保留为内联虚线 chip -- "慢慢标关键词"的
  // 过程不因权威到达而倒退消失（用户点了下钻也能走 local_* 链路）
  const concepts = useMemo(() => {
    if (ownConcepts.length === 0) return [...candidates, ...treeConcepts]
    if (candidates.length === 0) return ownConcepts
    const covered = new Set()
    for (const c of ownConcepts) {
      covered.add(c.canonical_name)
      for (const a of (c.aliases || [])) covered.add(a)
    }
    const extra = candidates.filter((c) => !covered.has(c.canonical_name))
    return extra.length > 0 ? [...ownConcepts, ...extra] : ownConcepts
  }, [ownConcepts, candidates, treeConcepts])
  const { unmatched } = useMemo(
    () => buildInlineSegments(layer.answer || '', concepts),
    [layer.answer, concepts]
  )
  // unmatched 跨层去重：祖先层已标识的概念不在本层 unmatched 块重复显示。
  // 候选/种子词不进 unmatched 块(它们本来就在正文里内联了，再罗列是噪声)
  const unmatchedDedup = useMemo(() => {
    const real = unmatched.filter((c) => !c.candidate)
    if (real.length === 0) return real
    const seen = new Set()
    for (const n of getPathNodes()) {
      if (n.qa_id === layer.qa_id) break  // 到本层为止
      for (const c of (n.concepts || [])) {
        if (c.concept_id) seen.add(c.concept_id)
      }
    }
    if (seen.size === 0) return real
    return real.filter((c) => !c.concept_id || !seen.has(c.concept_id))
  }, [unmatched, layer.qa_id])

  // 复制原文 + toast 反馈
  const [copied, setCopied] = useState(false)
  // 针对性提问（页边追问）：就选段/就这层发问，问题长成探索树子层
  const [askAnchor, setAskAnchor] = useState(null)   // { kind, text, label } | null（null=关）
  const [asking, setAsking] = useState(false)

  // 选中气泡（InlineAnswer 内嵌组件）经事件总线开提问框
  useEffect(() => {
    const onAskSelection = (e) => setAskAnchor(e.detail)
    window.addEventListener('starmind:askSelection', onAskSelection)
    return () => window.removeEventListener('starmind:askSelection', onAskSelection)
  }, [])

  // 子层 SSE 订阅（onDrill / onAsk 共用：drilldown -> pushLayer -> subscribe）
  function subscribeChild(childQaId) {
    api.subscribeStream(childQaId, {
      answer_delta: (ev) => {
        const cur = findNode(childQaId)
        updateLayer(childQaId, { answer: (cur?.answer || '') + ev.text })
      },
      status: (ev) => updateLayer(childQaId, { status: ev.status }),
      concepts: (ev) => updateLayer(childQaId, { concepts: ev.concepts }),
      concept_candidates: (ev) => {
        const cur = findNode(childQaId)
        const prev = cur?.candidates || []
        updateLayer(childQaId, { candidates: [...prev, ...ev.concepts] })
      },
      search_sources: (ev) => updateLayer(childQaId, { searchSources: ev.sources }),
      layer_summary: (ev) => updateLayer(childQaId, { layer_summary: ev.layer_summary }),
      done: () => { updateLayer(childQaId, { loading: false }); clearInflight() },
      error: () => { updateLayer(childQaId, { loading: false }); clearInflight() },
    })
  }

  // 提问：mode=ask 走 drilldown 链路（问题原样作子层 question，不概念包装）
  async function onAskSubmit(question) {
    if (!guardAction(layer.qa_id)) return
    setAsking(true)
    try {
      const child = await api.drillDown(layer.qa_id, `local_ask_${Date.now()}`, question, 'ask')
      const { pushLayer } = await import('../store/qaStore')
      pushLayer({
        qa_id: child.qa_id, question: child.question || question, answer: '',
        status: 'generating', concepts: [], layer_summary: '', loading: true,
        context: child.context || null,
        origin: 'ask',          // 提问层：树上显示原始问题 + 问章
      })
      subscribeChild(child.qa_id)
      setAskAnchor(null)  // 成功后关对话框
    } catch (err) {
      console.error('[onAskSubmit] 提问失败:', err)
      clearInflight()
    } finally {
      setAsking(false)
    }
  }

  async function onCopy() {
    const text = layer.answer || ''
    let ok = false
    try {
      await navigator.clipboard.writeText(text)
      ok = true
    } catch {
      // 回退:临时 textarea + execCommand(权限被拒/非 HTTPS 环境)
      try {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'; ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        ok = document.execCommand('copy')
        document.body.removeChild(ta)
      } catch { ok = false }
    }
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    }
  }

  return (
    <div style={styles.wrap}>
      <AskDialog
        open={!!askAnchor}
        anchor={askAnchor}
        submitting={asking}
        onSubmit={onAskSubmit}
        onClose={() => setAskAnchor(null)}
      />
      {copied && (
        <div style={styles.toastWrap}>
          <span style={styles.toast}>已复制原文</span>
        </div>
      )}
      {/* 固定顶栏:历史翻页器(不随正文滚动被卷走,始终可达) */}
      <div style={styles.topBar}>
        <span style={styles.topBarDepth}>第 {depth} 层</span>
        <div style={styles.pager} aria-label="探索历史翻页">
          <button
            style={{ ...styles.pageBtn, ...(!canBack ? styles.pageBtnDisabled : {}) }}
            onClick={goBack}
            disabled={!canBack}
            title={canBack ? '后退（Alt+←）' : '已是最早一页'}
            aria-label="后退"
          >‹</button>
          <span style={styles.pageCount}>{historyIdx + 1} / {historyLen}</span>
          <button
            style={{ ...styles.pageBtn, ...(!canForward ? styles.pageBtnDisabled : {}) }}
            onClick={goForward}
            disabled={!canForward}
            title={canForward ? '前进（Alt+→）' : '已是最新一页'}
            aria-label="前进"
          >›</button>
        </div>
        <span style={styles.topBarHint}>Alt+←/→ 翻页 · 最多记 30 步</span>
        <ReaderControls />
      </div>
      <div style={styles.scrollArea}>
      <div style={styles.scrollWrap}>
        {/* 层标签 + 生长茎(签名元素) */}
        <header style={styles.layerHeader}>
          <div style={styles.stem} aria-hidden="true">
            <span style={styles.stemNode} />
          </div>
          <div style={styles.layerMeta}>
            <h1 style={styles.layerQ}>{layer.question}</h1>
            {layer.loading && <span style={styles.loadingDot} title="生成中" />}
            {layer.answer && (
              <button
                style={styles.askBtn}
                onClick={() => setAskAnchor({ kind: 'layer', text: layer.question, label: '就这层追问' })}
                disabled={!!inflight && inflight !== layer.qa_id}
                title="就这层内容提出追问，问题会长成子层"
              >追问</button>
            )}
            {layer.answer && (
              <button
                className="copyBtn"
                style={{ ...styles.copyBtn, ...(copied ? styles.copyBtnDone : {}) }}
                onClick={onCopy}
                title="复制原始 markdown"
                aria-label="复制原文"
              >{copied ? '已复制' : '复制'}</button>
            )}
          </div>
        </header>

        {/* 下钻相关材料上下文 —— 克制卷宗索引气质,不喧宾夺主 */}
        {layer.context && layer.context.snippets && layer.context.snippets.length > 0 && (
          <ContextBlock context={layer.context} />
        )}

        {/* 回答正文 + 内联概念 —— markdown 渲染,衬线书本感 */}
        <article style={styles.answer}>
          {layer.answer ? (
            <InlineAnswer answer={layer.answer} concepts={concepts} layer={layer} inflight={inflight} />
          ) : (
            <div style={styles.generating}>
              {layer.loading ? <WaitingHint /> : '（空回答）'}
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

        {/* 联网搜索来源:时效性回答的参考出处(墨蓝卷宗条目) */}
        {layer.searchSources && layer.searchSources.length > 0 && (
          <SearchSourcesBlock sources={layer.searchSources} />
        )}


        {/* 未匹配的概念(抽取了但正文没出现)。
            仅权威列表到位后显示--流式期间 concepts 是树上已有概念的投影,
            大多不在本层正文,显示会造成满屏无关 chip */}
        {ownConcepts.length > 0 && unmatchedDedup.length > 0 && (
          <div style={styles.unmatchedBlock}>
            <div style={styles.unmatchedLabel}>抽取但正文未出现</div>
            <div style={styles.unmatchedChips}>
              {unmatchedDedup.map((c) => (
                <ConceptInline key={c.concept_id} concept={c} inflight={inflight} layer={layer} />
              ))}
            </div>
          </div>
        )}

        {/* 选中创建提示 —— 静默脚注,不抢正文 */}
        <div style={styles.hint}>选中正文可标为概念下钻，也可就选段或就这层提问</div>
      </div>
      </div>
    </div>
  )
}

// —— 下钻相关材料上下文:从原文检索的段落,克制展示,不喧宾夺主 ——
// 视觉层级低于正文:暖纸底 + 陶土棕细左边线 + 衬线小号,带"相关材料"标签
// 篇幅收窄:默认最多2段,每段截断~120字,超出折叠
function truncateText(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}
function ContextBlock({ context }) {
  const [expanded, setExpanded] = useState(false)
  const snippets = context.snippets || []
  const preparation = context.preparation || 0
  const matched = context.matched_chunks || 0
  const total = context.total_chunks || 0
  const MAX_INITIAL = 2
  const CHAR_LIMIT = 120
  const shown = expanded ? snippets : snippets.slice(0, MAX_INITIAL)
  const hiddenCount = snippets.length - MAX_INITIAL
  return (
    <section style={styles.contextBlock} aria-label="相关学习材料">
      <div style={styles.contextHead}>
        <span style={styles.contextLabel}>相关材料</span>
        <span style={styles.contextMeta}>
          {matched}/{total} 段相关 · 准备度 {Math.round(preparation * 100)}%
        </span>
      </div>
      <div style={styles.contextBody}>
        {shown.map((s, i) => {
          const text = expanded ? s : truncateText(s, CHAR_LIMIT)
          return (
            <div key={i} style={styles.contextSnippet}>
              {renderMarkdown(text, [], () => null)}
            </div>
          )
        })}
        {!expanded && hiddenCount > 0 && (
          <button style={styles.contextToggle} onClick={() => setExpanded(true)}>
            展开剩余 {hiddenCount} 段
          </button>
        )}
        {expanded && snippets.length > MAX_INITIAL && (
          <button style={styles.contextToggle} onClick={() => setExpanded(false)}>
            收起
          </button>
        )}
      </div>
    </section>
  )
}

// -- 联网搜索来源:时效性回答的参考出处 --
// 墨蓝色调(与相关材料的陶土棕区分:联网新知 vs 本地材料)
function SearchSourcesBlock({ sources }) {
  const [expanded, setExpanded] = useState(false)
  const MAX_INITIAL = 3
  const shown = expanded ? sources : sources.slice(0, MAX_INITIAL)
  return (
    <section style={styles.sourcesBlock} aria-label="联网搜索来源">
      <div style={styles.sourcesHead}>
        <span style={styles.sourcesLabel}>联网来源</span>
        <span style={styles.sourcesMeta}>{sources.length} 条参考</span>
      </div>
      <div style={styles.sourcesBody}>
        {shown.map((s, i) => (
          <a key={i} style={styles.sourcesItem} href={s.url} target="_blank"
             rel="noopener noreferrer" title={s.snippet}>
            <span style={styles.sourcesTitle}>{s.title || s.url}</span>
            <span style={styles.sourcesHost}>{hostOf(s.url)}</span>
          </a>
        ))}
        {!expanded && sources.length > MAX_INITIAL && (
          <button style={styles.sourcesToggle} onClick={() => setExpanded(true)}>
            展开剩余 {sources.length - MAX_INITIAL} 条
          </button>
        )}
      </div>
    </section>
  )
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www[.]/, '') } catch { return '' }
}


// —— 内联回答:markdown 渲染正文,概念位置渲染为可点击的内联 chip ——
// -- 等待提示:分阶段文案 --
// 实测网关 TTFT 16-38s(排队主导),恒显"正在落笔…"会让用户在长等待里
// 失去耐心。8s 前是正常落笔节奏;8s 后坦白"排队中"并说明原因,
// 诚实的等待比虚假的"正在写"更能留人。
function WaitingHint() {
  const [stage, setStage] = useState(0)
  useEffect(() => {
    const id = setTimeout(() => setStage(1), 8000)
    return () => clearTimeout(id)
  }, [])
  return (
    <span style={styles.waitingRow}>
      <span style={styles.waitingDot} aria-hidden="true" />
      {stage === 0 ? '正在落笔…' : '推理排队中，网关高峰期可能稍慢，请稍候…'}
    </span>
  )
}

function InlineAnswer({ answer, concepts, layer, inflight }) {
  const [selection, setSelection] = useState(null)
  const articleRef = useRef(null)

  // 流式渲染节流：answer_delta 高频到达(网关倾泻期每~27ms一个)时，
  // 每个 delta 都全量重跑 renderMarkdown(O(正文×概念)切分)会饱和主线程，
  // 正文"卡顿式跳出"、候选 chip 渐次点亮的过程也被吞掉。
  // 本地显示副本 100ms 刷新一次(渲染次数降 ~75%)；
  // 流结束(loading=false)立即同步，不影响终态。
  const [displayAnswer, setDisplayAnswer] = useState(answer)
  useEffect(() => {
    if (answer === displayAnswer) return
    if (!layer.loading) { setDisplayAnswer(answer); return }
    const id = setTimeout(() => setDisplayAnswer(answer), 100)
    return () => clearTimeout(id)
  }, [answer, layer.loading, displayAnswer])

  // 概念渲染回调:markdown 渲染器在概念位置调它,返回内联 ConceptInline
  const renderConcept = (concept) => (
    <ConceptInline concept={concept} inflight={inflight} layer={layer} inline />
  )

  function handleMouseUp() {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed) { setSelection(null); return }
    const text = sel.toString().trim()
    // 上限 120：长选段是"就选段提问"的典型场景（如整句技术表述）。
    // "标为概念"按钮另行按 ≤20 渲染——概念是短名词短语，长句不当概念。
    if (text.length < 2 || text.length > 120) { setSelection(null); return }
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
    if (!guardAction(layer.qa_id)) return
    setLastViewed(layer.qa_id, null)
    const localId = `local_${Date.now()}`
    try {
      // 本地选中文本还没有 concept_id，correctAnnotation(add) 需要 UUID concept_id
      // 必 422，跳过；等子层 SSE 抽取出真概念后由后端归一化建节点
      const child = await api.drillDown(layer.qa_id, localId, text)
      const { pushLayer } = await import('../store/qaStore')
      pushLayer({
        qa_id: child.qa_id, question: child.question || text, answer: '',
        status: 'generating', concepts: [], layer_summary: '', loading: true,
        context: child.context || null,
        origin: 'concept',      // 概念层：树上只显示概念名
        displayLabel: text,
      })
      api.incrementExplore(localId)
      api.subscribeStream(child.qa_id, {
        answer_delta: (ev) => {
          const cur = findNode(child.qa_id)
          updateLayer(child.qa_id, { answer: (cur?.answer || '') + ev.text })
        },
        status: (ev) => updateLayer(child.qa_id, { status: ev.status }),
        concepts: (ev) => updateLayer(child.qa_id, { concepts: ev.concepts }),
        concept_candidates: (ev) => {
          const cur = findNode(child.qa_id)
          const prev = cur?.candidates || []
          updateLayer(child.qa_id, { candidates: [...prev, ...ev.concepts] })
        },
        search_sources: (ev) => updateLayer(child.qa_id, { searchSources: ev.sources }),
        layer_summary: (ev) => updateLayer(child.qa_id, { layer_summary: ev.layer_summary }),
        done: () => { updateLayer(child.qa_id, { loading: false }); clearInflight() },
        error: () => { updateLayer(child.qa_id, { loading: false }); clearInflight() },
      })
    } catch (err) {
      console.error('[onCreateAndDrill] 下钻失败:', err)
      clearInflight()
    } finally {
      setSelection(null)
      window.getSelection()?.removeAllRanges()
    }
  }

  return (
    <div ref={articleRef} style={styles.articleInner} onMouseUp={handleMouseUp}>
      {renderMarkdown(displayAnswer, concepts, renderConcept)}
      {selection && (
        <div style={{ ...styles.popover, left: selection.x, top: selection.y }}>
          {selection.text.length <= 20 && (
            <button style={styles.popoverBtn} onClick={() => onCreateAndDrill(selection.text)}>
              标为概念·下钻
            </button>
          )}
          <button
            style={{ ...styles.popoverBtn, ...styles.popoverBtnAsk }}
            onClick={() => {
              window.dispatchEvent(new CustomEvent('starmind:askSelection', {
                detail: { kind: 'selection', text: selection.text, label: selection.text.length <= 20 ? '就选段追问' : '就长选段追问' },
              }))
              setSelection(null)
              window.getSelection()?.removeAllRanges()
            }}
            title="针对选段提出问题，问题长成子层"
          >
            就选段提问
          </button>
        </div>
      )}
    </div>
  )
}

// —— 内联概念 chip（可点击下钻）——
function ConceptInline({ concept, inflight, layer, inline }) {
  const [showTip, setShowTip] = useState(false)
  const [drilling, setDrilling] = useState(false)
  const understood = concept.understood || (concept.explore_count >= 2)
  const tier = tierForCount(concept.explore_count || 0)
  const name = concept.canonical_name || concept.name
  // 候选/种子词:本地临时高亮,无 concept_id,下钻走"标为概念"链路
  const isCandidate = !!concept.candidate && !concept.concept_id

  async function onDrill() {
    // guardAction 传当前层 qa_id：本层在途允许，别的层在途才拒（原传 null 导致 inflight 非空即锁死）
    if (understood || drilling) return
    if (!guardAction(layer.qa_id)) return
    setDrilling(true)
    setLastViewed(layer.qa_id, concept.concept_id)
    try {
      // 候选无 concept_id:走 local_* 链路(后端 _is_uuid 守卫跳过历史查询,
      // 子层抽取归一化后建真节点),与"标为概念下钻"一致
      const conceptId = concept.concept_id || `local_${Date.now()}`
      const child = await api.drillDown(layer.qa_id, conceptId, name)
      const { pushLayer } = await import('../store/qaStore')
      pushLayer({
        qa_id: child.qa_id, question: child.question || name, answer: '',
        status: 'generating', concepts: [], layer_summary: '', loading: true,
        context: child.context || null,
        origin: 'concept',      // 概念层：树上只显示概念名
        displayLabel: name,
      })
      if (concept.concept_id) api.incrementExplore(concept.concept_id)
      api.subscribeStream(child.qa_id, {
        answer_delta: (ev) => {
          const cur = findNode(child.qa_id)
          updateLayer(child.qa_id, { answer: (cur?.answer || '') + ev.text })
        },
        status: (ev) => updateLayer(child.qa_id, { status: ev.status }),
        concepts: (ev) => updateLayer(child.qa_id, { concepts: ev.concepts }),
        concept_candidates: (ev) => {
          const cur = findNode(child.qa_id)
          const prev = cur?.candidates || []
          updateLayer(child.qa_id, { candidates: [...prev, ...ev.concepts] })
        },
        search_sources: (ev) => updateLayer(child.qa_id, { searchSources: ev.sources }),
        layer_summary: (ev) => updateLayer(child.qa_id, { layer_summary: ev.layer_summary }),
        done: () => { updateLayer(child.qa_id, { loading: false }); clearInflight() },
        error: () => { updateLayer(child.qa_id, { loading: false }); clearInflight() },
      })
    } catch (err) {
      console.error('[onDrill] 下钻失败:', err)
      clearInflight()
    } finally {
      setDrilling(false)
    }
  }

  const busy = drilling
  if (inline) {
    // 内联在正文里:圆角按钮高亮(墨蓝浅底/陶土棕已理解),不抢正文
    // 候选(无 concept_id):虚线边框轻样式--"疑似概念"与权威概念可区分,
    // 点击同样可下钻
    const understoodStyle = understood ? {
      background: 'var(--settled-soft)',
      color: 'var(--settled)',
      border: '1px solid var(--settled-soft)',
      cursor: 'default',
    } : isCandidate ? {
      background: 'transparent',
      color: 'var(--active-ink)',
      border: '1px dashed var(--active-soft)',
      cursor: busy ? 'progress' : 'pointer',
    } : busy ? {
      background: 'var(--active-soft)',
      color: 'var(--active-ink)',
      border: '1px solid var(--active-soft)',
      cursor: 'progress',
      opacity: 0.7,
    } : {
      background: 'var(--active-soft)',
      color: 'var(--active-ink)',
      border: '1px solid var(--active-soft)',
      cursor: 'pointer',
    }
    return (
      <span
        style={{ ...styles.inline, ...understoodStyle }}
        data-testid="concept-chip"
        data-variant={isCandidate ? 'candidate' : 'inline'}
        onMouseEnter={() => setShowTip(true)}
        onMouseLeave={() => setShowTip(false)}
        onClick={onDrill}
        title={understood ? '已理解' : (busy ? '下钻中…' : (isCandidate ? '候选概念 · 点击下钻' : '点击下钻'))}
      >
        {busy ? `${name}…` : name}
        {showTip && !busy && (
          <span style={styles.tooltip}>
            {understood ? '已理解' : isCandidate
              ? '候选概念 · 点击下钻'
              : `点击下钻 · 置信度 ${(concept.confidence * 100).toFixed(0)}%`}
          </span>
        )}
      </span>
    )
  }
  // 非内联（unmatched 块）：pill 样式
  return (
    <button
      data-testid="concept-chip"
      data-variant="pill"
      style={{
        ...styles.chip,
        background: understood ? 'var(--settled-soft)' : TIER_COLOR[tier],
        color: understood ? 'var(--settled)' : '#fff',
        opacity: understood ? 0.6 : (busy ? 0.6 : 1),
        cursor: understood ? 'not-allowed' : (busy ? 'progress' : 'pointer'),
      }}
      onClick={onDrill}
      disabled={understood || busy}
    >
      {busy ? `${name}…` : name}
    </button>
  )
}

// —— 把正文按概念首次出现位置切分成 segments ——
// -- 流式动态高亮:树上概念并集的 memo 依赖键 --
// currentPath 每次重渲染引用不稳,取路径 qa_id 串作稳定键
function currentPathKey() {
  return getState().currentPath.join('>')
}

// -- question 种子词:流式第 0 秒就可参与正文高亮 --
// 用户点名的概念(下钻问句「X」)和英文术语(LLM/RAG/CAP)在问题里就有,
// 不必等抽取。正文出现即刻高亮 -- "想找的概念最快返回"的第一通道。
const SEED_EN = /[A-Za-z][A-Za-z0-9+.-]{1,19}/g
const SEED_QUOTED = /「([^「」]{2,12})」/g
function extractSeedTerms(question) {
  const out = new Set()
  if (!question) return []
  // 「X」引号词整体(下钻包装问句的核心概念,如「一致性哈希」)
  for (const m of question.matchAll(SEED_QUOTED)) {
    if (m[1].length <= 8) out.add(m[1])
  }
  // 英文术语词元
  for (const m of question.matchAll(SEED_EN)) {
    out.add(m[0])
  }
  return [...out]
}

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
  // wrap 是列布局容器不滚动:顶栏固定 + 滚动下放 scrollArea
  wrap: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'var(--paper)', overflow: 'hidden' },
  // 固定顶栏:历史翻页器驻地,不随正文滚动,底部发丝线与阅读区分界
  topBar: {
    flexShrink: 0, display: 'flex', alignItems: 'center', gap: 14,
    padding: '8px 36px', borderBottom: '1px solid var(--rule-soft)',
    maxWidth: 748, width: '100%', margin: '0 auto',
  },
  topBarDepth: {
    fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--active)',
    background: 'var(--active-soft)', borderRadius: 'var(--r-sm)', padding: '2px 8px',
    letterSpacing: '0.04em', flexShrink: 0,
  },
  topBarHint: {
    marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 10.5,
    color: 'var(--ink-faint)', letterSpacing: '0.04em',
  },
  // 滚动区:承载正文(滚动条只属于这里)
  scrollArea: { flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column' },
  // 阅读区:max-width 680(中文长行 40-55 字舒适),上下加大呼吸
  scrollWrap: { maxWidth: 680, margin: '0 auto', padding: '32px 36px 56px', width: '100%', minHeight: '100%', position: 'relative' },
  // 空态:加一根短茎暗示"等一个问题落下"
  empty: { flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', padding: 48, background: 'var(--paper)', gap: 14, position: 'relative' },
  emptyTools: { position: 'absolute', top: 18, right: 36 },
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
  // 翻页器（顶栏版）:浅纸底凹槽感,呼吸融入顶栏
  pager: {
    display: 'inline-flex', alignItems: 'center', gap: 0, flexShrink: 0,
    border: '1px solid var(--rule)', borderRadius: 'var(--r-sm)',
    background: 'var(--paper-soft)', overflow: 'hidden',
  },
  pageBtn: {
    width: 22, height: 22, border: 'none', background: 'transparent',
    cursor: 'pointer', color: 'var(--active)', fontFamily: 'var(--serif)',
    fontSize: 16, lineHeight: 1, padding: 0, transition: 'background 0.15s, color 0.15s',
  },
  pageBtnDisabled: {
    color: 'var(--ink-faint)', cursor: 'not-allowed', opacity: 0.45,
  },
  pageCount: {
    fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-soft)',
    padding: '0 8px', minWidth: 38, textAlign: 'center', letterSpacing: '0.04em',
    borderLeft: '1px solid var(--rule-soft)', borderRight: '1px solid var(--rule-soft)',
    lineHeight: '22px',
  },
  // 复制原文按钮:mono 小字,低透明度 hover 浮现,陶土棕"已复制"反馈
  copyBtn: {
    flexShrink: 0, marginLeft: 'auto', padding: '3px 10px',
    border: '1px solid var(--rule)', borderRadius: 'var(--r-sm)',
    background: 'var(--paper)', color: 'var(--ink-soft)',
    fontFamily: 'var(--mono)', fontSize: 10.5, cursor: 'pointer',
    letterSpacing: '0.04em', opacity: 0.5, transition: 'all 0.15s',
  },
  copyBtnDone: {
    color: 'var(--settled)', border: '1px solid var(--settled)', opacity: 1,
    background: 'var(--settled-soft)',
  },
  // 复制成功 toast:顶部居中浮现
  toastWrap: {
    position: 'fixed', top: 72, left: 0, right: 0, zIndex: 50,
    display: 'flex', justifyContent: 'center', pointerEvents: 'none',
  },
  toast: {
    background: 'var(--ink)', color: 'var(--paper)',
    padding: '6px 16px', borderRadius: 'var(--r-pill)',
    fontFamily: 'var(--mono)', fontSize: 12, letterSpacing: '0.04em',
    boxShadow: '0 4px 16px rgba(0,0,0,0.18)',
    animation: 'inkFadeIn 0.2s ease-out',
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
  // 等待提示行:呼吸点 + 文案
  waitingRow: {
    display: 'inline-flex', alignItems: 'center', gap: 8,
  },
  waitingDot: {
    width: 6, height: 6, borderRadius: '50%', background: 'var(--active)',
    animation: 'pulse 1.4s ease-in-out infinite', flexShrink: 0,
  },
  generating: { color: 'var(--ink-soft)', fontStyle: 'italic', fontFamily: 'var(--serif)', fontSize: 'var(--fs-body)' },
  inline: {
    fontWeight: 500, position: 'relative',
    display: 'inline', padding: '1px 7px', margin: '0 1px',
    borderRadius: 'var(--r-pill)', fontSize: '0.92em',
    transition: 'background 0.15s, color 0.15s, border-color 0.15s',
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
  // —— 下钻相关材料上下文:卷宗索引气质,层级低于正文 ——
  contextBlock: {
    margin: '0 0 20px', padding: '12px 16px',
    background: 'var(--paper-warm)', borderLeft: '2px solid var(--settled)',
    borderRadius: 'var(--r-sm)', opacity: 0.92,
  },
  contextHead: {
    display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
    marginBottom: 8, paddingBottom: 6, borderBottom: '1px dotted var(--rule-soft)',
  },
  contextLabel: {
    fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--settled)',
    textTransform: 'uppercase', letterSpacing: '0.1em',
  },
  contextMeta: {
    fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--ink-faint)',
    letterSpacing: '0.04em',
  },
  contextBody: { display: 'flex', flexDirection: 'column', gap: 8 },
  contextSnippet: {
    fontFamily: 'var(--serif)', fontSize: 13, lineHeight: 1.65,
    color: 'var(--ink-soft)', letterSpacing: '0.01em',
    padding: 0, margin: 0,
  },
  contextToggle: {
    alignSelf: 'flex-start', background: 'none', border: 'none', cursor: 'pointer',
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--settled)',
    letterSpacing: '0.04em', padding: '2px 0', marginTop: 2,
  },
  // 联网搜索来源块(墨蓝:联网新知,区别于本地材料的陶土棕)
  sourcesBlock: {
    marginTop: 20, padding: '10px 14px', background: 'var(--active-soft)',
    borderRadius: 'var(--r-md)', borderLeft: '3px solid var(--active)',
  },
  sourcesHead: {
    display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
    marginBottom: 8, paddingBottom: 6, borderBottom: '1px dotted var(--rule-soft)',
  },
  sourcesLabel: {
    fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--active-ink)',
    textTransform: 'uppercase', letterSpacing: '0.1em',
  },
  sourcesMeta: {
    fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--ink-faint)',
    letterSpacing: '0.04em',
  },
  sourcesBody: { display: 'flex', flexDirection: 'column', gap: 5 },
  sourcesItem: {
    display: 'flex', alignItems: 'baseline', gap: 8, textDecoration: 'none',
    color: 'var(--ink)', fontSize: 12.5, fontFamily: 'var(--serif)',
    lineHeight: 1.4,
  },
  sourcesTitle: {
    flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
    whiteSpace: 'nowrap', borderBottom: '1px dotted var(--active)',
  },
  sourcesHost: {
    fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--active)',
    flexShrink: 0, letterSpacing: '0.03em',
  },
  sourcesToggle: {
    alignSelf: 'flex-start', background: 'none', border: 'none', cursor: 'pointer',
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--active-ink)',
    letterSpacing: '0.04em', padding: '2px 0', marginTop: 2,
  },
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
  // 选中气泡里的"提问"按钮：墨蓝描边幽灵态，与"标为概念"实心态区分--
  // 追问是开放动作（用户自己写），标概念是收敛动作（交给模型展开）
  popoverBtnAsk: {
    background: 'transparent', color: 'var(--paper)',
    border: '1px solid rgba(250,248,243,0.45)',
  },
  // 层头部"追问"按钮：与"复制"同级的轻量文字钮，衬线体手记气质
  askBtn: {
    background: 'none', border: '1px solid var(--rule)', color: 'var(--ink-soft)',
    padding: '3px 10px', fontSize: 11, cursor: 'pointer', borderRadius: 'var(--r-sm)',
    fontFamily: 'var(--serif)', letterSpacing: '0.04em', transition: 'all 0.15s',
  },
  hint: {
    marginTop: 28, fontSize: 11, color: 'var(--ink-faint)', textAlign: 'center',
    fontFamily: 'var(--mono)', letterSpacing: '0.04em',
  },
}
