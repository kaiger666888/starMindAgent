import React, { useEffect, useState, useCallback } from 'react'
import * as api from '../api/client'

// 复习视图：第二天的自我测验室。
// 与档案（暖米纸）区分：这里用冷调纸色 + 墨青专注色，安静克制。
// 交互即英雄：卡片正面只露问题 → 「理解 / 忘记」→ 翻面浮现答案印证 →
// 「下一个 / 明天再试」。翻面 reveal 是整页唯一的动效。
// streak 3 格刻度直接表达规则：连续 3 天理解才归档。

const UID_KEY = 'starMindAgent.uid'

function loadUid() {
  const u = localStorage.getItem(UID_KEY)
  if (u) return u
  localStorage.setItem(UID_KEY, 'default')
  return 'default'
}

// —— 评分语义（与后端 grade 一致）——
// understood: streak+1，连续 3 天归档；forgot: 重置，次日再到期；
// retry（明天再试）: 重置，次日再到期。忘记后先翻面印证再决定去留。

export default function ReviewView() {
  const [uid] = useState(loadUid)
  const [queue, setQueue] = useState([])
  const [progress, setProgress] = useState(null)
  const [cards, setCards] = useState([])          // 卡片库（含归档）
  const [current, setCurrent] = useState(null)    // 正在复习的卡
  const [revealed, setRevealed] = useState(false) // 是否已翻面
  const [graded, setGraded] = useState(null)      // 刚评分的结果 {grade, just_archived}
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('review')        // review | library

  const loadAll = useCallback(async () => {
    setError(null)
    try {
      const [p, q, all] = await Promise.all([
        api.reviewProgress(uid),
        api.dueCards(uid),
        api.allCards(uid),
      ])
      setProgress(p)
      setQueue(q)
      setCards(all)
      setCurrent(q[0] || null)
      setRevealed(false)
      setGraded(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [uid])

  useEffect(() => { loadAll() }, [loadAll])

  // —— 盲 check 动作 ——
  async function onGrade(grade) {
    if (!current || busy) return
    setBusy(true)
    setError(null)
    try {
      const g = await api.gradeCard(current.card_id, grade)
      setGraded(g)
      // 就地更新队列里的这张卡（不可变：换数组引用）
      setQueue((prev) => prev.map((c) => (c.card_id === g.card_id ? { ...c, ...g } : c)))
      if (grade === 'understood') {
        // 理解 → 立刻翻面印证答案
        setRevealed(true)
      }
      // forgot：先翻面看答案印证，再由用户决定下一个/明天再试
      if (grade !== 'understood') setRevealed(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  function nextCard() {
    // 从队列取下一张未处理的
    const rest = queue.filter((c) => c.card_id !== current?.card_id && c.status === 'active')
    setCurrent(rest[0] || null)
    setRevealed(false)
    setGraded(null)
    loadAll() // 刷新进度 + 卡片库
  }

  const pending = queue.filter((c) => c.status === 'active')
  const archived = cards.filter((c) => c.status === 'archived')

  return (
    <div style={s.wrap}>
      <header style={s.header}>
        <h2 style={s.title}>复习</h2>
        <span style={s.uid}>@{uid}</span>
      </header>

      {error && <div style={s.error}>{error}</div>}

      {/* —— 进度区 —— */}
      <ProgressStrip progress={progress} loading={loading} />

      {/* —— Tab：今日复习 / 卡片库 —— */}
      <div style={s.tabRow}>
        <button
          style={{ ...s.tabBtn, ...(tab === 'review' ? s.tabBtnOn : null) }}
          onClick={() => setTab('review')}
        >
          今日复习{pending.length > 0 ? ` · ${pending.length}` : ''}
        </button>
        <button
          style={{ ...s.tabBtn, ...(tab === 'library' ? s.tabBtnOn : null) }}
          onClick={() => setTab('library')}
        >
          卡片库 · {archived.length}
        </button>
      </div>

      {tab === 'review' ? (
        <ReviewPanel
          loading={loading}
          current={current}
          pendingCount={pending.length}
          revealed={revealed}
          graded={graded}
          busy={busy}
          onGrade={onGrade}
          onNext={nextCard}
        />
      ) : (
        <LibraryPanel cards={cards} onDelete={async (id) => {
          await api.deleteCard(id)
          loadAll()
        }} />
      )}
    </div>
  )
}

// —— 进度条：总数 / 在途 / 今日到期 / 已归档 + streak 刻度说明 ——
function ProgressStrip({ progress, loading }) {
  if (loading || !progress) {
    return <div style={s.progressEmpty}>正在清点卡片…</div>
  }
  const items = [
    { label: '今日到期', value: progress.due_now, accent: 'var(--active)' },
    { label: '在途', value: progress.in_flight, accent: 'var(--ink-soft)' },
    { label: '已归档', value: progress.archived, accent: 'var(--done)' },
  ]
  return (
    <section style={s.progressRow}>
      {items.map((it) => (
        <div key={it.label} style={s.progressCell}>
          <span style={{ ...s.progressNum, color: it.accent }}>{it.value}</span>
          <span style={s.progressLabel}>{it.label}</span>
        </div>
      ))}
      <div style={s.progressRule}>
        连续 <b>3</b> 天理解 → 归档
      </div>
    </section>
  )
}

// —— 复习面板：盲 check 主交互 ——
function ReviewPanel({ loading, current, pendingCount, revealed, graded, busy, onGrade, onNext }) {
  if (loading) return <div style={s.empty}>正在取出今天的卡片…</div>
  if (!current) {
    return (
      <div style={s.empty}>
        <div style={s.emptyTitle}>今天没有到期的卡片</div>
        <div style={s.emptyDesc}>
          在探索页提问、下钻概念、或选中正文「存为卡片」，<br />
          第二天它们会出现在这里等你 check。
        </div>
      </div>
    )
  }

  const justArchived = graded?.just_archived
  return (
    <section style={s.reviewArea}>
      <div style={{ ...s.card, ...(revealed ? s.cardRevealed : null) }}>
        {/* 卡头：概念名 + streak 刻度 */}
        <div style={s.cardHead}>
          <span style={s.cardConcept}>{current.concept_name || '自由卡片'}</span>
          <StreakTicks streak={current.streak} />
        </div>
        {/* 正面：测试问题 */}
        <div style={s.cardQuestion}>{current.question}</div>
        {/* 背面：答案印证（翻面 reveal） */}
        {revealed && (
          <div style={s.cardAnswer}>
            <div style={s.answerLabel}>答案</div>
            <div style={s.answerBody}>{current.answer}</div>
            {current.source_answer && (
              <details style={s.sourceDetails}>
                <summary style={s.sourceSummary}>回看原文出处</summary>
                <div style={s.sourceBody}>{current.source_answer}</div>
              </details>
            )}
          </div>
        )}
        {justArchived && (
          <div style={s.archivedBadge}>已归档 ✓ 连续 3 天理解</div>
        )}
      </div>

      {/* —— 动作区：两段式 —— */}
      <div style={s.actions}>
        {!graded ? (
          <>
            <button style={{ ...s.btn, ...s.btnUnderstand }} disabled={busy} onClick={() => onGrade('understood')}>
              理解
            </button>
            <button style={{ ...s.btn, ...s.btnForgot }} disabled={busy} onClick={() => onGrade('forgot')}>
              忘记
            </button>
          </>
        ) : (
          <>
            <span style={s.gradeEcho}>
              {graded.grade === 'understood' ? '已记为理解' : '已标记忘记'}
              {graded.grade === 'understood' && !justArchived && ` · 连续第 ${graded.streak} 天`}
              {graded.grade !== 'understood' && ' · 明天再来'}
            </span>
            <button style={{ ...s.btn, ...s.btnNext }} onClick={onNext}>
              下一个{pendingCount > 1 ? `（还剩 ${pendingCount - 1}）` : ''}
            </button>
            {graded.grade === 'understood' && (
              <button
                style={{ ...s.btn, ...s.btnGhost }}
                onClick={() => onGrade('forgot')}
                title="翻面后觉得自己其实记错了 → 改判忘记，明天再试"
              >
                记错了，明天再试
              </button>
            )}
          </>
        )}
      </div>
      {!revealed && !graded && (
        <div style={s.hint}>先凭记忆回忆要点，再点「理解 / 忘记」对照答案印证</div>
      )}
    </section>
  )
}

// —— streak 3 格刻度：连续理解天数可视化（2 格满即差 1 天归档）——
function StreakTicks({ streak }) {
  return (
    <span style={s.ticks} title={`连续理解 ${streak}/3 天`}>
      {[0, 1, 2].map((i) => (
        <span key={i} style={{ ...s.tick, ...(i < streak ? s.tickOn : null) }} />
      ))}
    </span>
  )
}

// —— 卡片库：全部卡片（含归档）——
function LibraryPanel({ cards, onDelete }) {
  const [confirmId, setConfirmId] = useState(null)
  if (!cards.length) {
    return <div style={s.empty}>还没有卡片。在探索页选中正文「存为卡片」，或勾选学习层让后台自动总结。</div>
  }
  return (
    <div style={s.libList}>
      {cards.map((c) => (
        <div key={c.card_id} style={s.libCard}>
          <div style={s.libHead}>
            <span style={s.libConcept}>{c.concept_name || '自由卡片'}</span>
            {c.status === 'archived' ? (
              <span style={s.libArchived}>已归档</span>
            ) : (
              <StreakTicks streak={c.streak} />
            )}
          </div>
          <div style={s.libQ}>{c.question}</div>
          <div style={s.libA}>{c.answer}</div>
          <div style={s.libMeta}>
            {c.review_count > 0 && <span>复习 {c.review_count} 次</span>}
            {c.last_grade && <span>上次：{c.last_grade === 'understood' ? '理解' : c.last_grade === 'forgot' ? '忘记' : '明天再试'}</span>}
            <span>建卡 {c.created_at?.slice(0, 10)}</span>
            {confirmId === c.card_id ? (
              <>
                <button style={s.libDelConfirm} onClick={() => { onDelete(c.card_id); setConfirmId(null) }}>确认删除</button>
                <button style={s.libDelCancel} onClick={() => setConfirmId(null)}>取消</button>
              </>
            ) : (
              <button style={s.libDel} onClick={() => setConfirmId(c.card_id)}>删除</button>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// —— 样式：冷调纸 + 墨青专注色，与档案页暖米色区隔 ——
const s = {
  wrap: { maxWidth: 860, margin: '0 auto', padding: '32px 24px 80px' },
  header: { display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 20 },
  title: { fontFamily: 'var(--serif)', fontSize: 24, fontWeight: 600, margin: 0, color: 'var(--ink)' },
  uid: { fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-faint)' },
  error: {
    padding: '10px 14px', background: 'var(--danger-soft)', color: 'var(--danger)',
    borderRadius: 'var(--r-sm)', fontSize: 13, marginBottom: 16,
  },

  // 进度区：一行数字 + 一句规则，不做卡片堆
  progressRow: {
    display: 'flex', alignItems: 'flex-end', gap: 36,
    padding: '18px 22px', background: 'var(--paper-soft)',
    borderRadius: 'var(--r-md)', marginBottom: 20, flexWrap: 'wrap',
  },
  progressCell: { display: 'flex', flexDirection: 'column', gap: 2 },
  progressNum: { fontFamily: 'var(--serif)', fontSize: 28, fontWeight: 600, lineHeight: 1 },
  progressLabel: { fontSize: 12, color: 'var(--ink-soft)' },
  progressRule: {
    marginLeft: 'auto', fontSize: 12, color: 'var(--ink-soft)',
    borderLeft: '1px solid var(--rule)', paddingLeft: 20, paddingBottom: 4,
  },
  progressEmpty: { fontSize: 13, color: 'var(--ink-faint)', padding: '12px 0 20px' },

  // Tab
  tabRow: { display: 'flex', gap: 2, marginBottom: 24, borderBottom: '1px solid var(--rule)' },
  tabBtn: {
    padding: '8px 18px', fontSize: 13, border: 'none', background: 'transparent',
    cursor: 'pointer', color: 'var(--ink-soft)', fontFamily: 'var(--sans)',
    borderBottom: '2px solid transparent', marginBottom: -1,
  },
  tabBtnOn: { color: 'var(--ink)', borderBottomColor: 'var(--active)', fontWeight: 500 },

  // 复习区
  reviewArea: { display: 'flex', flexDirection: 'column', alignItems: 'center' },
  card: {
    width: '100%', maxWidth: 640, background: 'var(--paper)',
    border: '1px solid var(--rule)', borderRadius: 'var(--r-md)',
    padding: '28px 32px', minHeight: 260, display: 'flex', flexDirection: 'column',
    transition: 'border-color 0.3s',
  },
  cardRevealed: { borderColor: 'var(--active)' },
  cardHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 },
  cardConcept: {
    fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--ink-faint)',
    letterSpacing: '0.06em',
  },
  ticks: { display: 'inline-flex', gap: 4 },
  tick: {
    width: 16, height: 4, borderRadius: 2, background: 'var(--rule-soft)',
    transition: 'background 0.2s',
  },
  tickOn: { background: 'var(--done)' },
  cardQuestion: {
    fontFamily: 'var(--serif)', fontSize: 21, lineHeight: 1.6, color: 'var(--ink)',
    flex: 1,
  },
  cardAnswer: { marginTop: 24, paddingTop: 20, borderTop: '1px dashed var(--rule)' },
  answerLabel: {
    fontSize: 10, fontFamily: 'var(--mono)', color: 'var(--active-ink)',
    letterSpacing: '0.1em', marginBottom: 8,
  },
  answerBody: { fontSize: 15, lineHeight: 1.9, color: 'var(--ink-read)', whiteSpace: 'pre-wrap' },
  sourceDetails: { marginTop: 14 },
  sourceSummary: {
    fontSize: 12, color: 'var(--ink-faint)', cursor: 'pointer', userSelect: 'none',
  },
  sourceBody: {
    marginTop: 8, fontSize: 13, lineHeight: 1.8, color: 'var(--ink-soft)',
    background: 'var(--paper-soft)', padding: '10px 14px', borderRadius: 'var(--r-sm)',
    whiteSpace: 'pre-wrap',
  },
  archivedBadge: {
    marginTop: 18, alignSelf: 'flex-start', padding: '4px 12px',
    background: 'var(--done-soft)', color: 'var(--done)', fontSize: 12,
    borderRadius: 'var(--r-sm)',
  },

  // 动作区
  actions: {
    display: 'flex', gap: 12, marginTop: 28, alignItems: 'center', flexWrap: 'wrap',
    justifyContent: 'center',
  },
  btn: {
    padding: '10px 28px', fontSize: 14, borderRadius: 'var(--r-sm)',
    border: '1px solid transparent', cursor: 'pointer', fontFamily: 'var(--sans)',
    transition: 'opacity 0.15s',
  },
  btnUnderstand: { background: 'var(--active)', color: '#fff' },
  btnForgot: { background: 'transparent', color: 'var(--ink-soft)', borderColor: 'var(--rule)' },
  btnNext: { background: 'var(--active)', color: '#fff' },
  btnGhost: { background: 'transparent', color: 'var(--danger)', borderColor: 'var(--danger-soft)', fontSize: 13, padding: '10px 16px' },
  gradeEcho: { fontSize: 13, color: 'var(--ink-soft)' },
  hint: { marginTop: 16, fontSize: 12, color: 'var(--ink-faint)' },

  // 卡片库
  libList: { display: 'flex', flexDirection: 'column', gap: 14 },
  libCard: {
    background: 'var(--paper)', border: '1px solid var(--rule-soft)',
    borderRadius: 'var(--r-md)', padding: '16px 20px',
  },
  libHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  libConcept: { fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--ink-faint)' },
  libArchived: { fontSize: 11, color: 'var(--done)' },
  libQ: { fontFamily: 'var(--serif)', fontSize: 15, color: 'var(--ink)', marginBottom: 6 },
  libA: { fontSize: 13, lineHeight: 1.8, color: 'var(--ink-soft)' },
  libMeta: {
    display: 'flex', gap: 14, marginTop: 10, fontSize: 11,
    color: 'var(--ink-faint)', alignItems: 'center', flexWrap: 'wrap',
  },
  libDel: { marginLeft: 'auto', border: 'none', background: 'transparent', color: 'var(--ink-faint)', cursor: 'pointer', fontSize: 11 },
  libDelConfirm: { marginLeft: 'auto', border: 'none', background: 'var(--danger-soft)', color: 'var(--danger)', cursor: 'pointer', fontSize: 11, padding: '2px 8px', borderRadius: 'var(--r-sm)' },
  libDelCancel: { border: 'none', background: 'transparent', color: 'var(--ink-faint)', cursor: 'pointer', fontSize: 11 },

  // 空态
  empty: { textAlign: 'center', padding: '60px 20px', color: 'var(--ink-faint)' },
  emptyTitle: { fontFamily: 'var(--serif)', fontSize: 17, color: 'var(--ink-soft)', marginBottom: 10 },
  emptyDesc: { fontSize: 13, lineHeight: 2 },
}
