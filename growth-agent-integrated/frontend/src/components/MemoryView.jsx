import React, { useEffect, useState, useMemo } from 'react'
import * as api from '../api/client'

// 学习记忆视图：个人成长档案室。
// 与主区（探索工具感、白底蓝 accent）区隔，这里用暖米纸 + 陶土棕 + 衬线标题，
// 像翻开一本学习日志。三段叙事：画像 → 概念诊断 → 学习足迹。

const UID_KEY = 'starMindAgent.uid'

// uid 读取:与 TreeView 一致——未存则用 'default' (而非随机串),
// 保证提问落库的 user_id 与档案查询的 user_id 一致,否则学习足迹查不到。
function loadUid() {
  const u = localStorage.getItem(UID_KEY)
  if (u) return u
  localStorage.setItem(UID_KEY, 'default')
  return 'default'
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
      let [p, s] = await Promise.all([
        api.getProfile(id).catch(() => null),
        api.listSessions(id).catch(() => []),
      ])
      // 智能回退:若查不到足迹且 uid 非 default,可能是旧的随机 u_xxx uid
      // 尝试用 default 重查一次(default 是 TreeView 未存 uid 时的默认落库值)
      if ((!s || s.length === 0) && id !== 'default') {
        const s2 = await api.listSessions('default').catch(() => [])
        if (s2 && s2.length > 0) {
          localStorage.setItem(UID_KEY, 'default')
          setUid('default')
          p = await api.getProfile('default').catch(() => null)
          s = s2
        }
      }
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
                onDeleted={() => setSessions((prev) => prev.filter((x) => x.session_id !== sess.session_id))}
              />
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}

// 画像卡:summary 衬线大字 + recommendation 突出 + 三列概念诊断
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

function SessionItem({ sess, idx, expanded, onToggle, onDeleted }) {
  const [detail, setDetail] = useState(null)
  const [loadingD, setLoadingD] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [resumeErr, setResumeErr] = useState(null)
  const [exporting, setExporting] = useState(false)
  const [confirmingDel, setConfirmingDel] = useState(false)
  const [deleting, setDeleting] = useState(false)
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

  // 结构化导出：md 学习手账（可再导入开新探索，与导入功能闭环）
  async function onExport() {
    setExporting(true)
    try {
      const blob = await api.exportSession(sess.session_id, 'md')
      const date = new Date().toISOString().slice(0, 10).replace(/-/g, '')
      const title = (sess.last_question || '学习手账').slice(0, 20)
      api.downloadBlob(blob, `${title}-${date}.md`)
    } catch (e) {
      console.error('[onExport] 导出失败:', e)
    } finally {
      setExporting(false)
    }
  }

  // 继续探索:把历史会话恢复成探索树,切到探索视图
  async function onResume() {
    setResuming(true)
    setResumeErr(null)
    try {
      const { restoreSession } = await import('../store/qaStore')
      const { session_id, steps, conceptsById } = await api.resumeSession(sess.session_id)
      const ok = restoreSession(session_id, steps, conceptsById)
      if (!ok) throw new Error('该会话无可恢复的步骤')
      window.dispatchEvent(new CustomEvent('starmind:resumeSession'))
    } catch (e) {
      setResumeErr(e.message || '恢复失败')
    } finally {
      setResuming(false)
    }
  }

  // 删除足迹：两段式确认（点一次变"确认删除"再点才删），防误触
  async function onDelete() {
    if (!confirmingDel) { setConfirmingDel(true); return }
    setDeleting(true)
    try {
      await api.deleteSession(sess.session_id)
      onDeleted()
    } catch (e) {
      console.error('[onDelete] 删除失败:', e)
      setConfirmingDel(false)
    } finally {
      setDeleting(false)
    }
  }

  const isImported = sess.domain_tag === 'imported'

  // 导入文件:卷宗式折叠——标题做主标,展开只显摘要+入口,不铺全文
  if (isImported) {
    return (
      <li style={s.timelineItem}>
        <button style={{ ...s.timelineBtn, ...s.importedBtn }} onClick={onToggle}>
          <span style={s.importedMark} aria-label="导入文件">卷</span>
          <span style={s.timelineDate}>{formatTime(sess.created_at)}</span>
          <span style={s.importedTitle}>{sess.last_question || '（未命名材料）'}</span>
          <span style={s.timelineCount}>{sess.qa_count} 轮</span>
          <span style={s.chevron}>{expanded ? '−' : '+'}</span>
        </button>
        {expanded && (
          <div style={s.timelineDetail}>
            {loadingD && !detail ? (
              <div style={s.empty}>载入中…</div>
            ) : detail && detail.steps && detail.steps.length > 0 ? (
              <ImportedDetail
                detail={detail}
                resuming={resuming}
                resumeErr={resumeErr}
                onResume={onResume}
                onExport={onExport}
                exporting={exporting}
                deleteBtn={(
                  <DeleteBtn
                    confirming={confirmingDel}
                    deleting={deleting}
                    onClick={onDelete}
                    onReset={() => setConfirmingDel(false)}
                  />
                )}
              />
            ) : (
              <div style={s.empty}>无步骤数据</div>
            )}
          </div>
        )}
      </li>
    )
  }

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
            <>
              {detail.steps.map((step, i) => (
                <div key={step.qa_id} style={{ ...s.stepRow, marginLeft: (step.depth - 1) * 16 }}>
                  <span style={s.stepDepth}>L{step.depth}</span>
                  <div style={s.stepBody}>
                    <div style={s.stepQ}>{step.question}</div>
                    {step.answer && <div style={s.stepA}>{truncate(step.answer, 160)}</div>}
                  </div>
                </div>
              ))}
              <div style={s.resumeRow}>
                <button style={s.resumeBtn} onClick={onResume} disabled={resuming}>
                  {resuming ? '正在恢复…' : '继续探索 →'}
                </button>
                <button style={s.exportBtn} onClick={onExport} disabled={exporting}>
                  {exporting ? '导出中…' : '导出手账'}
                </button>
                <DeleteBtn
                  confirming={confirmingDel}
                  deleting={deleting}
                  onClick={onDelete}
                  onReset={() => setConfirmingDel(false)}
                />
                {resumeErr && <span style={s.resumeErr}>{resumeErr}</span>}
              </div>
            </>
          ) : (
            <div style={s.empty}>无步骤数据</div>
          )}
        </div>
      )}
    </li>
  )
}

// 导入文件展开详情:元信息条 + content_plain 摘要(不铺全文) + 子层探索索引
function ImportedDetail({ detail, resuming, resumeErr, onResume, onExport, exporting, deleteBtn }) {
  const steps = detail.steps || []
  const root = steps[0]  // L0 = 导入根层, question=文件名, answer=content_plain
  // 下钻出来的子层:用结构过滤(parent_qa_id 非空)而非 depth。
  // 导入材料根层 depth=0,子层 depth=1;提问树根层 depth=1,子层 depth=2。
  // 用 depth>=2 会漏掉导入材料的第一层下钻,故按 parent_qa_id 判定更稳。
  const children = steps.filter((st) => st.parent_qa_id)
  const charCount = (root.answer || '').length
  // 已抽取的概念数:所有层 extracted_concept_ids 去重
  const conceptIds = new Set()
  for (const st of steps) for (const id of (st.extracted_concept_ids || [])) conceptIds.add(id)
  // 折叠全文,只显前 300 字摘要
  const [showFull, setShowFull] = useState(false)
  const fullText = root.answer || ''
  const preview = showFull ? fullText : truncate(fullText, 300)
  return (
    <div>
      {/* 元信息条 */}
      <div style={s.importedMeta}>
        <span style={s.importedMetaItem}>导入 · {charCount.toLocaleString()} 字</span>
        <span style={s.importedMetaDot}>·</span>
        <span style={s.importedMetaItem}>{conceptIds.size} 个概念</span>
        {children.length > 0 && (
          <>
            <span style={s.importedMetaDot}>·</span>
            <span style={s.importedMetaItem}>{children.length} 处下钻</span>
          </>
        )}
      </div>
      {/* 原文摘要:衬线,克制,不喧宾夺主 */}
      {fullText && (
        <div style={s.importedPreview}>
          <div style={s.importedPreviewLabel}>原文摘要</div>
          <div style={s.importedPreviewText}>{preview}</div>
          {fullText.length > 300 && (
            <button style={s.importedPreviewToggle} onClick={() => setShowFull(!showFull)}>
              {showFull ? '收起' : `展开全文（${fullText.length.toLocaleString()} 字）`}
            </button>
          )}
        </div>
      )}
      {/* 子层探索索引:L2+ 的下钻点 */}
      {children.length > 0 && (
        <div style={s.importedChildren}>
          <div style={s.importedChildrenLabel}>下钻探索 · {children.length}</div>
          {children.map((st) => (
            <div key={st.qa_id} style={{ ...s.stepRow, marginLeft: (st.depth - root.depth) * 16 }}>
              <span style={s.stepDepth}>L{st.depth}</span>
              <div style={s.stepBody}>
                <div style={s.stepQ}>{st.question}</div>
                {st.answer && <div style={s.stepA}>{truncate(st.answer, 120)}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
      {/* 继续探索:从根层打开这棵树 */}
      <div style={s.resumeRow}>
        <button style={s.resumeBtn} onClick={onResume} disabled={resuming}>
          {resuming ? '正在恢复…' : '在探索视图中打开 →'}
        </button>
        <button style={s.exportBtn} onClick={onExport} disabled={exporting}>
          {exporting ? '导出中…' : '导出手账'}
        </button>
        {deleteBtn}
        {resumeErr && <span style={s.resumeErr}>{resumeErr}</span>}
      </div>
    </div>
  )
}

// 删除足迹按钮：两段式确认。第一次点变红色"确认删除"，
// 3s 未再点自动弹回；再点才真正调 DELETE。
function DeleteBtn({ confirming, deleting, onClick, onReset }) {
  useEffect(() => {
    if (!confirming) return
    const id = setTimeout(onReset, 3000)
    return () => clearTimeout(id)
  }, [confirming])
  const style = confirming ? {
    ...s.deleteBtn,
    background: 'var(--danger)', color: 'var(--paper)', borderColor: 'var(--danger)',
  } : s.deleteBtn
  return (
    <button
      style={style}
      onClick={onClick}
      disabled={deleting}
      title={confirming ? '再点一次确认删除' : '删除这条学习足迹'}
    >
      {deleting ? '删除中…' : (confirming ? '确认删除' : '删除')}
    </button>
  )
}

// Signature:学习曲线。根据 qa_count 画一条细线,隐喻成长轨迹。
function GrowthCurve({ qaCount }) {
  const path = useMemo(() => {
    const pts = []
    const W = 100, H = 24
    const amp = Math.min(8, 2 + qaCount * 0.5)
    for (let i = 0; i <= 20; i++) {
      const x = (i / 20) * W
      const noise = Math.sin(i * 1.3 + qaCount * 0.7) * amp
      const trend = (i / 20) * (H - 4)
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

// 概念诊断三栏:统一用 token 色彩,弱化饱和度,与档案陶土棕基调协调
const chipTone = {
  mastered: { background: 'var(--settled-soft)', color: 'var(--settled)', borderColor: '#C9B89E' },
  weak: { background: '#FBEEE0', color: 'var(--settled)', borderColor: '#D4A574' },
  interest: { background: 'var(--paper-warm)', color: 'var(--ink-soft)', borderColor: '#C7C0B0' },
}

const s = {
  wrap: {
    maxWidth: 720, margin: '0 auto', padding: '32px 24px 56px',
    color: 'var(--ink-read)',
    fontFamily: 'var(--sans)',
    lineHeight: 1.6,
  },
  curve: { color: 'var(--settled)', opacity: 0.4, marginBottom: 8, height: 24 },
  curveSvg: { width: '100%', height: '100%', display: 'block' },
  header: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 },
  title: {
    fontFamily: 'var(--serif)', fontSize: 28, fontWeight: 600, margin: 0, letterSpacing: '0.01em',
    color: 'var(--ink)',
  },
  uid: { fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink-soft)' },
  uidRow: { display: 'flex', gap: 6, marginBottom: 20 },
  uidInput: {
    flex: 1, padding: '6px 10px', fontSize: 12,
    border: '1px solid var(--rule)', borderRadius: 'var(--r-sm)', background: 'var(--paper)',
    fontFamily: 'var(--mono)', color: 'var(--ink)', outline: 'none',
  },
  uidBtn: {
    padding: '6px 12px', fontSize: 12, background: 'transparent', color: 'var(--settled)',
    border: '1px solid var(--settled)', borderRadius: 'var(--r-sm)', cursor: 'pointer',
    fontFamily: 'var(--sans)', transition: 'all 0.15s',
  },
  error: {
    padding: '8px 12px', marginBottom: 16, fontSize: 13,
    background: 'var(--danger-soft)', color: 'var(--danger)', borderRadius: 'var(--r-sm)',
    borderLeft: '3px solid var(--danger)',
    fontFamily: 'var(--sans)',
  },
  card: {
    background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 'var(--r-md)',
    padding: '22px 24px', marginBottom: 28, boxShadow: '0 1px 3px rgba(0,0,0,0.03)',
  },
  cardHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14, flexWrap: 'wrap', gap: 8 },
  cardLabel: {
    fontFamily: 'var(--mono)', fontSize: 11,
    color: 'var(--settled)', textTransform: 'uppercase', letterSpacing: '0.08em',
  },
  cardMeta: { fontSize: 12, color: 'var(--ink-soft)', fontFamily: 'var(--mono)' },
  stale: { color: 'var(--settled)' },
  summary: {
    fontFamily: 'var(--serif)', fontSize: 16.5, lineHeight: 1.7,
    margin: '0 0 16px', color: 'var(--ink-read)', letterSpacing: '0.01em',
  },
  recommendBox: {
    display: 'flex', gap: 12, padding: '12px 16px', marginBottom: 20,
    background: 'var(--paper-warm)', borderLeft: '3px solid var(--settled)', borderRadius: 'var(--r-sm)',
  },
  recommendLabel: {
    fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--settled)',
    textTransform: 'uppercase', letterSpacing: '0.08em', flexShrink: 0, paddingTop: 2,
  },
  recommendText: { fontSize: 14, color: 'var(--ink-read)', fontFamily: 'var(--sans)', lineHeight: 1.6 },
  conceptGrid: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 18, marginBottom: 20 },
  conceptCol: {},
  conceptColTitle: {
    fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-soft)',
    marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.06em',
    borderBottom: '1px solid var(--rule-soft)', paddingBottom: 5,
  },
  chipRow: { display: 'flex', flexWrap: 'wrap', gap: 4 },
  chip: { fontSize: 12, padding: '2px 8px', borderRadius: 10, border: '1px solid', fontFamily: 'var(--sans)' },
  conceptEmpty: { fontSize: 12, color: 'var(--ink-faint)', fontFamily: 'var(--mono)' },
  refreshBtn: {
    padding: '7px 16px', fontSize: 12, background: 'var(--settled)', color: 'var(--paper)',
    border: 'none', borderRadius: 'var(--r-sm)', cursor: 'pointer',
    fontFamily: 'var(--sans)', fontWeight: 500, transition: 'opacity 0.15s',
  },
  lastAt: { marginTop: 10, fontSize: 11, color: 'var(--ink-faint)', fontFamily: 'var(--mono)' },
  section: { marginBottom: 24 },
  sectionLabel: {
    fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--settled)',
    textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 14,
    borderBottom: '1px solid var(--rule-soft)', paddingBottom: 5,
  },
  empty: { fontSize: 14, color: 'var(--ink-soft)', lineHeight: 1.75, padding: '12px 0', fontFamily: 'var(--serif)' },
  timeline: { listStyle: 'none', padding: 0, margin: 0 },
  timelineItem: { borderBottom: '1px solid var(--rule-soft)' },
  timelineBtn: {
    width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '14px 4px',
    background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left',
    color: 'inherit', fontFamily: 'inherit', transition: 'background 0.15s',
  },
  timelineDate: { fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-faint)', flexShrink: 0, width: 110 },
  timelineQ: { flex: 1, fontSize: 14, color: 'var(--ink-read)', fontFamily: 'var(--serif)', lineHeight: 1.4 },
  timelineCount: { fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-soft)' },
  timelineDomain: {
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--settled)',
    background: 'var(--settled-soft)', padding: '1px 6px', borderRadius: 8,
  },
  chevron: { color: 'var(--ink-faint)', fontSize: 16, flexShrink: 0, fontFamily: 'var(--mono)' },
  timelineDetail: { padding: '4px 0 14px 110px' },
  stepRow: { display: 'flex', gap: 10, padding: '7px 0', borderLeft: '1px dotted var(--rule)', paddingLeft: 10 },
  stepDepth: {
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--settled)',
    background: 'var(--settled-soft)', borderRadius: 3, padding: '0 5px', height: 16, flexShrink: 0,
    display: 'inline-flex', alignItems: 'center',
  },
  stepBody: { flex: 1, minWidth: 0 },
  stepQ: { fontSize: 13, color: 'var(--ink-read)', fontWeight: 500, fontFamily: 'var(--serif)' },
  stepA: { fontSize: 12, color: 'var(--ink-soft)', marginTop: 3, lineHeight: 1.55, fontFamily: 'var(--serif)' },
  // 继续探索按钮:陶土棕描边 + 衬线,融入档案气质
  resumeRow: { marginTop: 14, display: 'flex', alignItems: 'center', gap: 12 },
  resumeBtn: {
    padding: '7px 18px', fontSize: 13, fontFamily: 'var(--serif)', fontWeight: 500,
    color: 'var(--settled)', background: 'var(--settled-soft)',
    border: '1px solid var(--settled)', borderRadius: 'var(--r-sm)',
    cursor: 'pointer', transition: 'opacity 0.15s', letterSpacing: '0.02em',
  },
  resumeErr: { fontSize: 11, color: 'var(--danger)', fontFamily: 'var(--mono)' },
  // 删除足迹按钮:危险动作,默认幽灵态(danger 描边极淡),确认态实心红
  deleteBtn: {
    marginLeft: 'auto', padding: '7px 14px', fontSize: 12, fontFamily: 'var(--sans)',
    color: 'var(--danger)', background: 'transparent',
    border: '1px solid var(--danger-soft)', borderRadius: 'var(--r-sm)',
    cursor: 'pointer', opacity: 0.75, letterSpacing: '0.02em',
  },
  // 导出手账按钮:同族次级(描边轻、透明底),导出是带走动作不是主流程
  exportBtn: {
    padding: '7px 16px', fontSize: 13, fontFamily: 'var(--serif)',
    color: 'var(--settled)', background: 'transparent',
    border: '1px dashed var(--settled)', borderRadius: 'var(--r-sm)',
    cursor: 'pointer', transition: 'opacity 0.15s', letterSpacing: '0.02em',
    opacity: 0.85,
  },

  // —— 导入文件:卷宗式折叠(陶土棕"卷"字标记 + 标题主标,展开只显摘要) ——
  importedBtn: {
    gap: 8, padding: '16px 4px',
  },
  importedMark: {
    fontFamily: 'var(--serif)', fontSize: 12, fontWeight: 600, color: 'var(--paper)',
    background: 'var(--settled)', borderRadius: 'var(--r-sm)',
    width: 22, height: 22, flexShrink: 0,
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    letterSpacing: 0,
  },
  importedTitle: {
    flex: 1, fontSize: 14.5, color: 'var(--ink)', fontFamily: 'var(--serif)',
    fontWeight: 500, lineHeight: 1.4, letterSpacing: '0.01em',
    display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
  },
  // 元信息条:mono 小字,低饱和,卷宗索引气质
  importedMeta: {
    display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
    fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-soft)',
    letterSpacing: '0.04em', marginBottom: 12, paddingBottom: 10,
    borderBottom: '1px dotted var(--rule-soft)',
  },
  importedMetaItem: { color: 'var(--settled)' },
  importedMetaDot: { color: 'var(--ink-faint)' },
  // 原文摘要:暖纸底 + 陶土棕细边,衬线小号,克制不喧宾夺主
  importedPreview: {
    background: 'var(--paper-warm)', borderLeft: '2px solid var(--settled)',
    borderRadius: 'var(--r-sm)', padding: '12px 14px', marginBottom: 14,
  },
  importedPreviewLabel: {
    fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--settled)',
    textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6,
  },
  importedPreviewText: {
    fontFamily: 'var(--serif)', fontSize: 13, lineHeight: 1.7, color: 'var(--ink-read)',
    letterSpacing: '0.01em', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
  },
  importedPreviewToggle: {
    marginTop: 8, background: 'none', border: 'none', cursor: 'pointer',
    fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--settled)',
    letterSpacing: '0.04em', padding: 0,
  },
  // 子层探索索引
  importedChildren: { marginBottom: 4 },
  importedChildrenLabel: {
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-faint)',
    textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8,
  },
}
