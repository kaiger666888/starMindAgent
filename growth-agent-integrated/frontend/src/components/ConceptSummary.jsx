// 概念汇总面板 —— 左侧 TreeView 树导航下方
// 收集当前会话所有概念(去重),按 explore_count 着色 + 完成度进度条
// 点击概念:派发 starmind:startFromConcept 事件,跳到探索视图预填
//
// 完成度映射:explore_count 0→10% 1→30% 2→60% 3→85% 4+→95% understood→100%

import { useStore, toggleConceptUnderstood } from '../store/qaStore'

// 完成度百分比
function completionPct(c) {
  if (c.understood) return 1
  const n = c.explore_count || 0
  if (n === 0) return 0.1
  if (n === 1) return 0.3
  if (n === 2) return 0.6
  if (n === 3) return 0.85
  return 0.95
}

// 色阶:0=灰,1=绿,2+=红阶(回访越深越红)
function tierColor(c) {
  if (c.understood) return 'var(--settled)'
  const n = c.explore_count || 0
  if (n === 0) return 'var(--tier-gray)'
  if (n === 1) return 'var(--tier-green)'
  if (n < 3) return 'var(--tier-red-1)'
  if (n < 4) return 'var(--tier-red-2)'
  return 'var(--tier-red-3)'
}

// 遍历树收集所有概念,按 concept_id 去重(合并 explore_count)
function collectConcepts(node, acc = {}) {
  if (!node) return acc
  for (const c of (node.concepts || [])) {
    const id = c.concept_id
    if (!id) continue
    if (acc[id]) {
      // 合并:取最大 explore_count
      acc[id].explore_count = Math.max(acc[id].explore_count || 0, c.explore_count || 0)
      if (c.understood) acc[id].understood = true
    } else {
      acc[id] = { ...c }
    }
  }
  for (const child of (node.children || [])) {
    collectConcepts(child, acc)
  }
  return acc
}

export default function ConceptSummary() {
  const tree = useStore((s) => s.tree)
  const concepts = tree ? Object.values(collectConcepts(tree)) : []
  concepts.sort((a, b) => completionPct(a) - completionPct(b))

  // 整体完成度:平均
  const overall = concepts.length
    ? concepts.reduce((s, c) => s + completionPct(c), 0) / concepts.length
    : 0

  if (concepts.length === 0) return null

  function onJump(concept) {
    // 派发:切探索视图 + 预填概念名作问题
    window.dispatchEvent(new CustomEvent('starmind:startFromConcept', {
      detail: concept.canonical_name || concept.name,
    }))
  }

  return (
    <div style={styles.wrap}>
      <div style={styles.header}>
        <span style={styles.label}>概念汇总</span>
        <span style={styles.count}>{concepts.length}</span>
      </div>
      {/* 整体完成度 */}
      <div style={styles.overallRow}>
        <div style={styles.overallBar}>
          <div style={{ ...styles.overallFill, width: `${overall * 100}%` }} />
        </div>
        <span style={styles.overallPct}>{Math.round(overall * 100)}%</span>
      </div>
      <div style={styles.list}>
        {concepts.map((c) => {
          const pct = completionPct(c)
          const color = tierColor(c)
          const name = c.canonical_name || c.name
          const understood = !!c.understood
          // 进度条式背景:深绿从左向右按完成度增长,understood 满格(与树条目同构)
          const alpha = understood ? 0.32 : 0.18
          const bg = `linear-gradient(to right, rgba(47,107,79,${alpha}) 0%, rgba(47,107,79,${alpha}) ${pct * 100}%, transparent ${pct * 100}%)`
          return (
            <div key={c.concept_id} style={{ display: 'flex', alignItems: 'stretch' }}>
              <button
                aria-label={understood ? '取消已理解' : '标记已理解'}
                style={{ ...styles.check, ...(understood ? styles.checkDone : null) }}
                onClick={() => toggleConceptUnderstood(c.concept_id)}
                title={understood ? '已理解(点击取消)' : '标记已理解'}
              >
                {understood && <svg width="9" height="8" viewBox="0 0 10 8" aria-hidden="true"><polyline points="1,4 3.8,6.5 9,1" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>}
              </button>
              <button
                style={{ ...styles.chip, background: bg }}
                onClick={() => onJump(c)}
                title={`${name} · 完成度 ${Math.round(pct * 100)}% · 探索 ${c.explore_count || 0} 次`}
              >
                <span style={styles.chipName}>{name}</span>
                <span style={styles.miniBar}>
                  <span style={{ ...styles.miniFill, width: `${pct * 100}%`, background: color }} />
                </span>
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const styles = {
  wrap: {
    marginTop: 20, paddingTop: 14, borderTop: '1px solid var(--rule-soft)',
  },
  header: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8 },
  label: {
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-soft)',
    textTransform: 'uppercase', letterSpacing: '0.08em',
  },
  count: { fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-faint)' },
  // 整体完成度:长条 + 百分比
  overallRow: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 },
  overallBar: {
    flex: 1, height: 4, background: 'var(--rule-soft)', borderRadius: 2, overflow: 'hidden',
  },
  overallFill: {
    height: '100%', background: 'linear-gradient(90deg, var(--active), var(--settled))',
    transition: 'width 0.4s ease',
  },
  overallPct: { fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-soft)', minWidth: 28, textAlign: 'right' },
  // 概念 list
  list: { display: 'flex', flexDirection: 'column', gap: 4 },
  // 概念理解 check 框：与 TreeView 行首 check 同构（深绿填充 + 白对钩）
  check: {
    width: 14, height: 14, flexShrink: 0, alignSelf: 'center', marginRight: 4,
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    background: 'var(--paper)', border: '1px solid var(--rule)',
    borderRadius: 'var(--r-sm)', cursor: 'pointer', padding: 0,
    transition: 'background 0.2s, border-color 0.2s',
  },
  checkDone: { background: 'var(--done)', borderColor: 'var(--done)' },
  chip: {
    display: 'flex', alignItems: 'center', gap: 8, width: '100%',
    padding: '5px 8px', border: 'none', background: 'transparent',
    cursor: 'pointer', borderRadius: 'var(--r-sm)',
    transition: 'background 0.15s', textAlign: 'left',
  },
  chipName: {
    flex: 1, fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink-read)',
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  },
  // 概念完成度 mini 进度条
  miniBar: {
    width: 40, height: 3, background: 'var(--rule-soft)', borderRadius: 2, overflow: 'hidden', flexShrink: 0,
  },
  miniFill: { height: '100%', borderRadius: 2, transition: 'width 0.4s ease' },
}
