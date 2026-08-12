import React, { useEffect, useState, useMemo } from 'react'
import * as api from '../api/client'

// 学习记忆视图：个人成长档案室。
// 与主区（探索工具感、白底蓝 accent）区隔，这里用暖米纸 + 陶土棕 + 衬线标题，
// 像翻开一本学习日志。三段叙事：画像 → 概念诊断 → 学习足迹。

const UID_KEY = 'starMindAgent.uid'

function loadUid() {
  const u = localStorage.getItem(UID_KEY)
  if (u) return u
  const next = `u_${Math.random().toString(36).slice(2, 8)}`
  localStorage.setItem(UID_KEY, next)
  return next
}

export default function MemoryView() {
  const [uid, setUid] = useState(loadUid)
  const [uidInput, setUidInput] = useState('')
  const [profile, setProfile] = useState(null)
  const [sessions, setSessions] = useState([])
  const [loadingProfile, setLoadingProfile] = useState(false)
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(null) // session_id

  async function loadAll(id) {
    setError(null)
    setLoadingSessions(true)
    try {
      const [p, s] = await Promise.all([
        api.getProfile(id).catch(() => null),
        api.listSessions(id).catch(() => []),
      ])
      setProfile(p)
      setSessions(s)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingSessions(false)
    }
  }

  useEffect(() => { loadAll(uid) }, [uid])

  async function onRefresh() {
    setRefreshing(true)
    setError(null)
    try {
      const r = await api.refreshProfile(uid, true)
      const p = await api.getProfile(uid).catch(() => null)
      setProfile(p)
      return r
    } catch (e) {
      setError(e.message)
    } finally {
      setRefreshing(false)
    }
  }

  function switchUid() {
    const v = uidInput.trim()
    if (!v) return
    localStorage.setItem(UID_KEY, v)
    setUid(v)
    setUidInput('')
  }

  return (
    <div style={s.wrap}>
      <GrowthCurve qaCount={profile?.qa_count || 0} />
      <header style={s.header}>
        <h2 style={s.title}>成长档案</h2>
        <span style={s.uid}>@{uid}</span>
      </header>

      <div style={s.uidRow}>
        <input
          style={s.uidInput}
          value={uidInput}
          onChange={(e) => setUidInput(e.target.value)}
          placeholder="切换用户标识（如 kai）"
          onKeyDown={(e) => e.key === 'Enter' && switchUid()}
        />
        <button style={s.uidBtn} onClick={switchUid}>切换</button>
      </div>

      {error && <div style={s.error}>{error}</div>}

      {/* —— 画像 —— */}
      <ProfileCard
        profile={profile}
        refreshing={refreshing}
        onRefresh={onRefresh}
        loading={loadingSessions && !profile}
      />

      {/* —— 学习足迹 —— */}
      <section style={s.section}>
        <div style={s.sectionLabel}>学习足迹</div>
        {loadingSessions && sessions.length === 0 ? (
          <div style={s.empty}>正在翻阅过往问答…</div>
        ) : sessions.length === 0 ? (
          <div style={s.empty}>
            还没有学习记录。<br />
            去左侧问第一个问题，开启你的探索树。
          </div>
        ) : (
          <ol style={s.timeline}>
            {sessions.map((sess, idx) => (
              <SessionItem
                key={sess.session_id}
                sess={sess}
                idx={idx}
                expanded={expanded === sess.session_id}
                onToggle={() => setExpanded(expanded === sess.session_id ? null : sess.session_id)}
              />
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}

// 画像卡：summary 衬线大字 + recommendation 突出 + 三列概念诊断
function ProfileCard({ profile, refreshing, onRefresh, loading }) {
  if (loading) {
    return <div style={s.card}><div style={s.empty}>正在汇总学习画像…</div></div>
  }
  if (!profile) {
    return (
      <div style={s.card}>
        <div style={s.empty}>
          还没有学习画像。<br />
          问答几轮后，点下方按钮让模型为你总结。
        </div>
        <button style={s.refreshBtn} onClick={onRefresh} disabled={refreshing}>
          {refreshing ? '正在总结…' : '生成学习画像'}
        </button>
      </div>
    )
  }
  const p = profile.profile || {}
  const mastered = p.mastered || []
  const weak = p.weak || []
  const interests = p.interests || []
  return (
    <section style={s.card}>
      <div style={s.cardHead}>
        <span style={s.cardLabel}>学习画像</span>
        <span style={s.cardMeta}>
          {profile.qa_count} 轮问答 · {profile.concept_count} 个概念
          {profile.summary_model && ` · ${profile.summary_model}`}
          {profile.stale && <span style={s.stale}> · 有新问答未纳入</span>}
        </span>
      </div>

      {p.summary && <p style={s.summary}>{p.summary}</p>}

      {p.recommendation && (
        <div style={s.recommendBox}>
          <span style={s.recommendLabel}>下一步</span>
          <span style={s.recommendText}>{p.recommendation}</span>
        </div>
      )}

      <div style={s.conceptGrid}>
        <ConceptCol title="已掌握" items={mastered} tone="mastered" />
        <ConceptCol title="待加强" items={weak} tone="weak" />
        <ConceptCol title="感兴趣" items={interests} tone="interest" />
      </div>

      <button style={s.refreshBtn} onClick={onRefresh} disabled={refreshing}>
        {refreshing ? '正在重新总结…' : (profile.stale ? '刷新画像（含新问答）' : '重新总结')}
      </button>
      {profile.last_summary_at && (
        <div style={s.lastAt}>
          上次总结于 {formatTime(profile.last_summary_at)}
        </div>
      )}
    </section>
  )
}

function ConceptCol({ title, items, tone }) {
  return (
    <div style={s.conceptCol}>
      <div style={s.conceptColTitle}>{title} · {items.length}</div>
      {items.length === 0 ? (
        <span style={s.conceptEmpty}>—</span>
      ) : (
        <div style={s.chipRow}>
          {items.map((c, i) => (
            <span key={i} style={{ ...s.chip, ...chipTone[tone] }}>{c}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function SessionItem({ sess, idx, expanded, onToggle }) {
  const [detail, setDetail] = useState(null)
  const [loadingD, setLoadingD] = useState(false)
  async function loadDetail() {
    if (detail) return
    setLoadingD(true)
    try {
      setDetail(await api.getSessionDetail(sess.session_id))
    } finally {
      setLoadingD(false)
    }
  }
  useEffect(() => { if (expanded) loadDetail() }, [expanded])
  return (
    <li style={s.timelineItem}>
      <button style={s.timelineBtn} onClick={onToggle}>
        <span style={s.timelineDate}>{formatTime(sess.created_at)}</span>
        <span style={s.timelineQ}>{sess.last_question || '（空问答）'}</span>
        <span style={s.timelineCount}>{sess.qa_count} 轮</span>
        {sess.domain_tag && <span style={s.timelineDomain}>{sess.domain_tag}</span>}
        <span style={s.chevron}>{expanded ? '−' : '+'}</span>
      </button>
      {expanded && (
        <div style={s.timelineDetail}>
          {loadingD && !detail ? (
            <div style={s.empty}>载入中…</div>
          ) : detail && detail.steps ? (
            detail.steps.map((step, i) => (
              <div key={step.qa_id} style={{ ...s.stepRow, marginLeft: (step.depth - 1) * 16 }}>
                <span style={s.stepDepth}>L{step.depth}</span>
                <div style={s.stepBody}>
                  <div style={s.stepQ}>{step.question}</div>
                  {step.answer && <div style={s.stepA}>{truncate(step.answer, 160)}</div>}
                </div>
              </div>
            ))
          ) : (
            <div style={s.empty}>无步骤数据</div>
          )}
        </div>
      )}
    </li>
  )
}

// Signature：学习曲线。根据 qa_count 画一条细线，隐喻成长轨迹。
function GrowthCurve({ qaCount }) {
  const path = useMemo(() => {
    // 用确定性伪随机生成一条上升+波动的曲线，长度固定但振幅随 qaCount 增长
    const pts = []
    const W = 100, H = 24
    const amp = Math.min(8, 2 + qaCount * 0.5)
    for (let i = 0; i <= 20; i++) {
      const x = (i / 20) * W
      const noise = Math.sin(i * 1.3 + qaCount * 0.7) * amp
      const trend = (i / 20) * (H - 4) // 整体上升
      const y = H - 2 - trend * 0.4 + noise * 0.3
      pts.push(`${x.toFixed(2)},${y.toFixed(2)}`)
    }
    return `M ${pts.join(' L ')}`
  }, [qaCount])
  return (
    <div style={s.curve} aria-hidden="true">
      <svg viewBox="0 0 100 24" preserveAspectRatio="none" style={s.curveSvg}>
        <path d={path} fill="none" stroke="currentColor" strokeWidth="0.6" strokeLinecap="round" />
      </svg>
    </div>
  )
}

// —— 工具 ——
function formatTime(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const pad = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch { return iso }
}
function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

const chipTone = {
  mastered: { background: '#E8DCCD', color: '#5C3A1F', borderColor: '#C9B89E' },
  weak: { background: '#FBEEE0', color: '#8B5A3C', borderColor: '#D4A574' },
  interest: { background: '#EFEBE3', color: '#5B6359', borderColor: '#C7C0B0' },
}

const s = {
  wrap: {
    maxWidth: 720, margin: '0 auto', padding: '24px 20px 48px',
    color: 'var(--mem-ink, #1F2421)',
    fontFamily: '-apple-system, "Segoe UI", "PingFang SC", sans-serif',
    lineHeight: 1.6,
  },
  curve: { color: '#8B5A3C', opacity: 0.4, marginBottom: 8, height: 24 },
  curveSvg: { width: '100%', height: '100%', display: 'block' },
  header: { display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4 },
  title: {
    fontFamily: '"Georgia", "Songti SC", serif',
    fontSize: 28, fontWeight: 400, margin: 0, letterSpacing: '0.01em',
  },
  uid: { fontFamily: '"JetBrains Mono", monospace', fontSize: 12, color: '#5B6359' },
  uidRow: { display: 'flex', gap: 6, marginBottom: 20 },
  uidInput: {
    flex: 1, padding: '5px 8px', fontSize: 12,
    border: '1px solid #D9D2C5', borderRadius: 4, background: '#fff',
    fontFamily: '"JetBrains Mono", monospace',
  },
  uidBtn: {
    padding: '5px 10px', fontSize: 12, background: 'transparent', color: '#8B5A3C',
    border: '1px solid #8B5A3C', borderRadius: 4, cursor: 'pointer',
  },
  error: {
    padding: '8px 12px', marginBottom: 16, fontSize: 13,
    background: '#FBEEE0', color: '#8B5A3C', borderRadius: 4,
    borderLeft: '3px solid #8B5A3C',
  },
  card: {
    background: '#FAF8F3', border: '1px solid #D9D2C5', borderRadius: 6,
    padding: '20px 22px', marginBottom: 24,
  },
  cardHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12, flexWrap: 'wrap', gap: 8 },
  cardLabel: {
    fontFamily: '"JetBrains Mono", monospace', fontSize: 11,
    color: '#8B5A3C', textTransform: 'uppercase', letterSpacing: '0.08em',
  },
  cardMeta: { fontSize: 12, color: '#5B6359', fontFamily: '"JetBrains Mono", monospace' },
  stale: { color: '#A8763B' },
  summary: {
    fontFamily: '"Georgia", "Songti SC", serif', fontSize: 17, lineHeight: 1.65,
    margin: '0 0 16px', color: '#1F2421',
  },
  recommendBox: {
    display: 'flex', gap: 10, padding: '10px 14px', marginBottom: 18,
    background: '#fff', borderLeft: '3px solid #8B5A3C', borderRadius: 2,
  },
  recommendLabel: {
    fontFamily: '"JetBrains Mono", monospace', fontSize: 11, color: '#8B5A3C',
    textTransform: 'uppercase', letterSpacing: '0.08em', flexShrink: 0, paddingTop: 2,
  },
  recommendText: { fontSize: 14, color: '#1F2421' },
  conceptGrid: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 18 },
  conceptCol: {},
  conceptColTitle: {
    fontFamily: '"JetBrains Mono", monospace', fontSize: 11, color: '#5B6359',
    marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em',
    borderBottom: '1px solid #D9D2C5', paddingBottom: 4,
  },
  chipRow: { display: 'flex', flexWrap: 'wrap', gap: 4 },
  chip: { fontSize: 12, padding: '2px 8px', borderRadius: 10, border: '1px solid' },
  conceptEmpty: { fontSize: 12, color: '#9A9388', fontFamily: '"JetBrains Mono", monospace' },
  refreshBtn: {
    padding: '6px 14px', fontSize: 12, background: '#8B5A3C', color: '#FAF8F3',
    border: 'none', borderRadius: 4, cursor: 'pointer',
  },
  lastAt: { marginTop: 8, fontSize: 11, color: '#9A9388', fontFamily: '"JetBrains Mono", monospace' },
  section: { marginBottom: 24 },
  sectionLabel: {
    fontFamily: '"JetBrains Mono", monospace', fontSize: 11, color: '#8B5A3C',
    textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12,
    borderBottom: '1px solid #D9D2C5', paddingBottom: 4,
  },
  empty: { fontSize: 14, color: '#5B6359', lineHeight: 1.7, padding: '12px 0' },
  timeline: { listStyle: 'none', padding: 0, margin: 0 },
  timelineItem: { borderBottom: '1px solid #E8E1D3' },
  timelineBtn: {
    width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '12px 4px',
    background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left',
    color: 'inherit', fontFamily: 'inherit',
  },
  timelineDate: { fontFamily: '"JetBrains Mono", monospace', fontSize: 11, color: '#9A9388', flexShrink: 0, width: 110 },
  timelineQ: { flex: 1, fontSize: 14, color: '#1F2421' },
  timelineCount: { fontFamily: '"JetBrains Mono", monospace', fontSize: 11, color: '#5B6359' },
  timelineDomain: {
    fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: '#8B5A3C',
    background: '#E8DCCD', padding: '1px 6px', borderRadius: 8,
  },
  chevron: { color: '#9A9388', fontSize: 16, flexShrink: 0 },
  timelineDetail: { padding: '4px 0 12px 110px' },
  stepRow: { display: 'flex', gap: 8, padding: '6px 0', borderLeft: '1px dotted #D9D2C5', paddingLeft: 8 },
  stepDepth: {
    fontFamily: '"JetBrains Mono", monospace', fontSize: 10, color: '#8B5A3C',
    background: '#E8DCCD', borderRadius: 3, padding: '0 4px', height: 16, flexShrink: 0,
  },
  stepBody: { flex: 1, minWidth: 0 },
  stepQ: { fontSize: 13, color: '#1F2421', fontWeight: 500 },
  stepA: { fontSize: 12, color: '#5B6359', marginTop: 2, lineHeight: 1.5 },
}
