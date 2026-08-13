import React, { useEffect, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import { useStore } from '../store/qaStore'
import * as api from '../api/client'

// 三状态视图（需求四节）：一 ⊂ 二 ⊂ 三
const VIEWS = [
  { key: 'user_click', label: '状态一·主动探索', desc: '点击下钻的概念间联系' },
  { key: 'co_occurrence', label: '状态二·共现', desc: '答案/问题中同现的概念联系' },
  { key: 'domain_graph', label: '状态三·领域扩展', desc: '已探索概念为种子，向外扩展1-2跳（导航入口）' },
]

// 节点热度对数四档着色（需求四节：1/2/4/8）——实际颜色值（cytoscape canvas 不读 CSS 变量）
const TIER_FILL = {
  gray: '#9ca3af',      // 未探索
  green: '#22c55e',     // 探索1次
  red_1: '#fca5a5',     // 2次（浅红，反复回访=重要）
  red_2: '#f87171',     // 4次
  red_3: '#ef4444',     // 8次
  red_4: '#b91c1c',     // >8（深红，反复下钻=复杂）
}
function tierForCount(c) {
  if (c <= 0) return 'gray'
  if (c === 1) return 'green'
  if (c < 2) return 'red_1'
  if (c < 4) return 'red_2'
  if (c < 8) return 'red_3'
  return 'red_4'
}

export default function ConceptGraph() {
  const cyRef = useRef(null)
  const containerRef = useRef(null)
  const currentPath = useStore((s) => s.currentPath)
  const activeSid = useStore((s) => s.activeSessionId)
  const [view, setView] = useState('user_click')  // 当前状态视图
  const [scope, setScope] = useState('session')    // session | global
  const [extensions, setExtensions] = useState(null)
  const [loadingExt, setLoadingExt] = useState(false)
  const [search, setSearch] = useState('')         // 概念搜索（需求六"检索已有概念"）
  const [selectedForMerge, setSelectedForMerge] = useState([])  // 手动合并多选（需求六）

  // 概念搜索：命中后高亮+居中（需求六"检索已有概念"）
  function onSearch(q) {
    setSearch(q)
    if (!cyRef.current || !q.trim()) {
      cyRef.current?.elements().removeClass('dimmed')
      return
    }
    const query = q.toLowerCase().trim()
    cyRef.current.elements().removeClass('dimmed')
    cyRef.current.nodes().filter((n) => {
      const label = (n.data('label') || '').toLowerCase()
      const aliases = n.data('aliases') || []
      return !label.includes(query) && !aliases.some((a) => a.toLowerCase().includes(query))
    }).addClass('dimmed')
    // 命中节点居中
    const hits = cyRef.current.nodes().filter((n) => {
      const label = (n.data('label') || '').toLowerCase()
      const aliases = n.data('aliases') || []
      return label.includes(query) || aliases.some((a) => a.toLowerCase().includes(query))
    })
    if (hits.length) {
      cyRef.current.animate({ center: { eles: hits.first(), zoom: 1.2 } }, { duration: 300 })
      hits.first().select()
    }
  }

  // 手动合并（需求六"手动合并概念"）：两概念合并为同一节点，走后端 audit log
  async function onMerge() {
    if (selectedForMerge.length !== 2) return
    const [a, b] = selectedForMerge
    if (!window.confirm(`合并这两个概念？合并 b 入 a，可通过 audit log 撤销。`)) return
    try {
      await api.mergeConcepts(a, b)
      setSelectedForMerge([])
      cyRef.current?.nodes().removeClass('merge-candidate')
      await loadGraph()
    } catch (e) {
      alert(`合并失败: ${e.message}`)
    }
  }

  function clearMergeSelection() {
    setSelectedForMerge([])
    cyRef.current?.nodes().removeClass('merge-candidate')
  }

  // 图数据：session 用 getGraph，global 用 getGlobalGraph
  async function loadGraph() {
    if (!cyRef.current) return
    let g
    if (scope === 'global') {
      const uid = localStorage.getItem('starMindAgent.uid') || 'default'
      g = await api.getGlobalGraph(uid)
    } else if (activeSid) {
      g = await api.getGraph(activeSid)
    } else {
      return
    }
    render(cyRef.current, g, view)
    // 切到状态三时加载扩展
    if (view === 'domain_graph' && activeSid && !extensions) {
      loadExtensions()
    }
  }

  async function loadExtensions() {
    if (!activeSid) return
    setLoadingExt(true)
    try {
      const ext = await api.extendDomainGraph(activeSid, 1)
      setExtensions(ext)
      // 把扩展的灰色概念节点加到图上
      if (cyRef.current && ext.extensions) {
        const greyNodes = []
        ext.extensions.forEach((item) => {
          item.related?.forEach((r) => {
            if (!r.is_explored && !cyRef.current.getElementById(`ext_${r.name}`).length) {
              greyNodes.push({
                data: {
                  id: `ext_${r.name}`, label: r.name,
                  explore_count: 0, is_extension: true,
                  reason: r.reason,
                },
                style: { 'background-color': TIER_FILL.gray, 'border-style': 'dashed' },
              })
            }
          })
        })
        cyRef.current.add(greyNodes)
        cyRef.current.layout({ name: 'cose', animate: true }).run()
      }
    } finally {
      setLoadingExt(false)
    }
  }

  useEffect(() => { loadGraph() }, [activeSid, currentPath.length, view, scope])

  useEffect(() => {
    if (!containerRef.current) return
    const cy = cytoscape({
      container: containerRef.current,
      style: [
        { selector: 'node', style: {
          'label': 'data(label)', 'text-valign': 'center', 'text-halign': 'center',
          'font-size': 12, 'color': '#1F2421', 'font-family': '"Source Serif 4", Georgia, serif',
          'font-weight': 500, 'width': 46, 'height': 46,
          'background-color': '#FAF8F3', 'border-width': 2, 'border-color': '#D9D2C5',
          'text-wrap': 'wrap', 'text-max-width': 70,
        }},
        { selector: 'node[is_extension = "true"]', style: {
          'border-style': 'dashed', 'border-width': 2, 'border-color': '#9A9388',
          'font-style': 'italic', 'color': '#9A9388',
        }},
        { selector: 'edge', style: {
          'line-color': '#D9D2C5', 'width': 1.2, 'curve-style': 'bezier',
          'target-arrow-shape': 'triangle', 'target-arrow-color': '#D9D2C5',
          'opacity': 0.55,
        }},
        { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#2B5F8A' } },
        { selector: 'node.dimmed', style: { opacity: 0.15 } },
        { selector: 'node.merge-candidate', style: { 'border-width': 3, 'border-color': '#B4544A', 'border-style': 'dashed' } },
      ],
      layout: { name: 'cose', animate: true, animateFilter: () => false,
        nodeRepulsion: () => 8000, idealEdgeLength: () => 90 },
    })
    cy.on('tap', 'node', (evt) => {
      const n = evt.target
      const qaId = n.data('qa_id')
      const isExt = n.data('is_extension')
      // Shift+点击：手动合并多选（需求六"手动合并概念"）
      if (evt.originalEvent.shiftKey) {
        const id = n.id()
        setSelectedForMerge((prev) => {
          if (prev.includes(id)) {
            n.removeClass('merge-candidate')
            return prev.filter((x) => x !== id)
          }
          if (prev.length >= 2) return prev  // 只选2个
          n.addClass('merge-candidate')
          return [...prev, id]
        })
        return
      }
      if (isExt) {
        // 状态三导航入口：点灰色未探索概念 → 以该概念名开新问题
        const name = n.data('label')
        if (window.confirm(`以「${name}」为新问题开启探索？`)) {
          window.dispatchEvent(new CustomEvent('starmind:startFromConcept', { detail: name }))
        }
      } else if (qaId) {
        api.drillDown(qaId, n.id(), n.data('label'))
      }
    })
    cyRef.current = cy
    return () => cy.destroy()
  }, [])

  function render(cy, g, currentView) {
    cy.elements().remove()
    // 状态包含关系：一 ⊂ 二 ⊂ 三
    // 状态一：只 user_click 边；状态二：user_click + co_occurrence；状态三：全部 + 扩展
    const viewOrder = ['user_click', 'co_occurrence', 'domain_graph']
    const allowedOrigins = viewOrder.slice(0, viewOrder.indexOf(currentView) + 1)
    const nodes = (g.nodes || []).map((n) => ({
      data: {
        id: n.concept_id, label: n.canonical_name,
        explore_count: n.explore_count || 0,
        understood: n.understood || false,
        aliases: n.aliases || [],
      },
      style: {
        'background-color': n.understood ? '#E8DCCD' : TIER_FILL[tierForCount(n.explore_count || 0)],
        opacity: n.understood ? 0.5 : 1,
        'border-color': n.understood ? '#8B5A3C' : '#D9D2C5',
      },
    }))
    const edges = (g.edges || []).filter((e) => allowedOrigins.includes(e.origin)).map((e) => ({
      data: { id: e.edge_id, source: e.source_id, target: e.target_id, origin: e.origin },
    }))
    cy.add([...nodes, ...edges])
    cy.layout({ name: 'cose', animate: true }).run()
  }

  const currentViewObj = VIEWS.find((v) => v.key === view)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, position: 'relative' }}>
      <div style={styles.toolbarTop}>
        <div style={styles.viewSwitch}>
          {VIEWS.map((v) => (
            <button
              key={v.key}
              style={{ ...styles.viewBtn, ...(view === v.key ? styles.viewBtnActive : {}) }}
              onClick={() => setView(v.key)}
              title={v.desc}
            >
              {v.label}
            </button>
          ))}
        </div>
        <div style={styles.scopeSwitch}>
          <button
            style={{ ...styles.scopeBtn, ...(scope === 'session' ? styles.scopeBtnActive : {}) }}
            onClick={() => setScope('session')}
            disabled={!activeSid}
          >本会话</button>
          <button
            style={{ ...styles.scopeBtn, ...(scope === 'global' ? styles.scopeBtnActive : {}) }}
            onClick={() => setScope('global')}
          >全局</button>
        </div>
      </div>
      <div style={styles.toolbarBottom}>
        <input
          style={styles.search}
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="搜索已探索概念…"
        />
        <div style={styles.legend}>
          <span style={styles.legendItem}><i style={{ ...styles.dot, background: TIER_FILL.gray }} />未探索</span>
          <span style={styles.legendItem}><i style={{ ...styles.dot, background: TIER_FILL.green }} />1次</span>
          <span style={styles.legendItem}><i style={{ ...styles.dot, background: TIER_FILL.red_1 }} />回访</span>
          <span style={styles.legendItem}><i style={{ ...styles.dot, background: TIER_FILL.red_3 }} />反复</span>
          <span style={styles.legendItem}><i style={{ ...styles.dot, background: '#e5e7eb', opacity: 0.5 }} />已理解</span>
        </div>
      </div>
      <div style={styles.viewDesc}>
        <span>{currentViewObj?.desc}</span>
        <span style={styles.viewHint}>Shift+点击两节点可手动合并</span>
      </div>
      {selectedForMerge.length > 0 && (
        <div style={styles.mergeBar}>
          已选 {selectedForMerge.length}/2 个概念
          {selectedForMerge.length === 2 && (
            <button style={styles.mergeBtn} onClick={onMerge}>合并</button>
          )}
          <button style={styles.clearBtn} onClick={clearMergeSelection}>取消</button>
        </div>
      )}
      <div ref={containerRef} style={styles.canvas} />
      {loadingExt && <div style={styles.loading}>正在扩展关联概念…</div>}
    </div>
  )
}

const styles = {
  toolbarTop: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '8px 12px 6px', borderBottom: '1px solid var(--rule-soft)', gap: 8, flexWrap: 'wrap',
  },
  toolbarBottom: {
    display: 'flex', alignItems: 'center', gap: 12,
    padding: '6px 12px 8px', borderBottom: '1px solid var(--rule-soft)', flexWrap: 'wrap',
  },
  viewSwitch: { display: 'flex', gap: 2, flexWrap: 'wrap' },
  viewBtn: {
    padding: '4px 10px', fontSize: 11, background: 'var(--paper)', color: 'var(--ink-soft)',
    border: '1px solid var(--rule-soft)', borderRadius: 'var(--r-sm)', cursor: 'pointer',
    fontFamily: 'var(--sans)', transition: 'all 0.15s',
  },
  viewBtnActive: { background: 'var(--active)', color: '#fff', borderColor: 'var(--active)' },
  search: {
    padding: '5px 10px', fontSize: 12, border: '1px solid var(--rule)', borderRadius: 'var(--r-sm)',
    width: 160, fontFamily: 'var(--sans)', background: 'var(--paper)', color: 'var(--ink)', outline: 'none',
  },
  scopeSwitch: { display: 'flex', gap: 2, background: 'var(--paper-soft)', borderRadius: 'var(--r-sm)', padding: 2 },
  scopeBtn: {
    padding: '3px 10px', fontSize: 11, background: 'transparent', color: 'var(--ink-soft)',
    border: 'none', borderRadius: 3, cursor: 'pointer', fontFamily: 'var(--mono)', transition: 'all 0.15s',
  },
  scopeBtnActive: { background: 'var(--paper)', color: 'var(--ink)', boxShadow: '0 1px 2px rgba(0,0,0,0.06)' },
  viewDesc: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8,
    fontSize: 10.5, color: 'var(--ink-faint)', padding: '5px 12px 6px',
    fontStyle: 'italic', fontFamily: 'var(--serif)', borderBottom: '1px solid var(--rule-soft)',
  },
  viewHint: { fontStyle: 'normal', fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--ink-faint)', letterSpacing: '0.04em' },
  legend: { display: 'flex', gap: 10, fontSize: 10, color: 'var(--ink-soft)', flexWrap: 'wrap', fontFamily: 'var(--mono)' },
  legendItem: { display: 'inline-flex', alignItems: 'center', gap: 3 },
  dot: { width: 9, height: 9, borderRadius: '50%', display: 'inline-block' },
  canvas: { flex: 1, minHeight: 300, background: 'var(--paper)' },
  loading: {
    position: 'absolute', top: 80, right: 20, padding: '4px 10px',
    background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 'var(--r-sm)',
    fontSize: 12, color: 'var(--ink-soft)', fontFamily: 'var(--sans)',
  },
  mergeBar: {
    display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px',
    background: 'var(--danger-soft)', borderBottom: '1px solid var(--danger)',
    fontSize: 12, color: 'var(--danger)', fontFamily: 'var(--sans)',
  },
  mergeBtn: { padding: '3px 10px', fontSize: 12, background: 'var(--danger)', color: '#fff', border: 'none', borderRadius: 'var(--r-sm)', cursor: 'pointer' },
  clearBtn: { padding: '3px 10px', fontSize: 12, background: 'transparent', color: 'var(--danger)', border: '1px solid var(--danger)', borderRadius: 'var(--r-sm)', cursor: 'pointer' },
}
