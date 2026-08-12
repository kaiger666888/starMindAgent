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

// 节点热度对数四档着色（需求四节：1/2/4/8）
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
  const stack = useStore((s) => s.stack)
  const activeSid = useStore((s) => s.activeSessionId)
  const [view, setView] = useState('user_click')  // 当前状态视图
  const [scope, setScope] = useState('session')    // session | global
  const [extensions, setExtensions] = useState(null)
  const [loadingExt, setLoadingExt] = useState(false)

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

  useEffect(() => { loadGraph() }, [activeSid, stack.length, view, scope])

  useEffect(() => {
    if (!containerRef.current) return
    const cy = cytoscape({
      container: containerRef.current,
      style: [
        { selector: 'node', style: {
          'label': 'data(label)', 'text-valign': 'center', 'text-halign': 'center',
          'font-size': 11, 'color': '#fff', 'width': 38, 'height': 38,
          'background-color': '#9ca3af', 'border-width': 1, 'border-color': '#fff',
        }},
        { selector: 'node[is_extension = "true"]', style: {
          'border-style': 'dashed', 'border-width': 2, 'border-color': '#6b7280',
          'font-style': 'italic',
        }},
        { selector: 'edge', style: {
          'line-color': '#cbd5e1', 'width': 1.5, 'curve-style': 'bezier',
          'target-arrow-shape': 'triangle', 'target-arrow-color': '#cbd5e1',
        }},
        { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#f59e0b' } },
      ],
      layout: { name: 'cose', animate: true, animateFilter: () => false,
        nodeRepulsion: () => 8000, idealEdgeLength: () => 90 },
    })
    cy.on('tap', 'node', (evt) => {
      const n = evt.target
      const qaId = n.data('qa_id')
      const isExt = n.data('is_extension')
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
      },
      style: {
        'background-color': n.understood ? '#e5e7eb' : TIER_FILL[tierForCount(n.explore_count || 0)],
        opacity: n.understood ? 0.5 : 1,
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
      <div style={styles.toolbar}>
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
      <div style={styles.viewDesc}>{currentViewObj?.desc}</div>
      <div style={styles.legend}>
        <span style={styles.legendItem}><i style={{ ...styles.dot, background: TIER_FILL.gray }} />未探索</span>
        <span style={styles.legendItem}><i style={{ ...styles.dot, background: TIER_FILL.green }} />探索1次</span>
        <span style={styles.legendItem}><i style={{ ...styles.dot, background: TIER_FILL.red_1 }} />2次(回访)</span>
        <span style={styles.legendItem}><i style={{ ...styles.dot, background: TIER_FILL.red_3 }} />反复(下钻)</span>
        <span style={styles.legendItem}><i style={{ ...styles.dot, background: '#e5e7eb', opacity: 0.5 }} />已理解</span>
      </div>
      <div ref={containerRef} style={styles.canvas} />
      {loadingExt && <div style={styles.loading}>正在扩展关联概念…</div>}
    </div>
  )
}

const styles = {
  toolbar: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 12px', borderBottom: '1px solid #e5e7eb', gap: 8, flexWrap: 'wrap' },
  viewSwitch: { display: 'flex', gap: 4 },
  viewBtn: { padding: '4px 10px', fontSize: 12, background: '#f3f4f6', color: '#6b7280', border: '1px solid #e5e7eb', borderRadius: 4, cursor: 'pointer' },
  viewBtnActive: { background: '#2563eb', color: '#fff', borderColor: '#2563eb' },
  scopeSwitch: { display: 'flex', gap: 2, background: '#f9fafb', borderRadius: 4, padding: 2 },
  scopeBtn: { padding: '3px 8px', fontSize: 11, background: 'transparent', color: '#6b7280', border: 'none', borderRadius: 3, cursor: 'pointer' },
  scopeBtnActive: { background: '#fff', color: '#1f2937', boxShadow: '0 1px 2px rgba(0,0,0,0.1)' },
  viewDesc: { fontSize: 11, color: '#9ca3af', padding: '4px 12px', fontStyle: 'italic' },
  legend: { display: 'flex', gap: 12, padding: '4px 12px', fontSize: 11, color: '#6b7280', flexWrap: 'wrap' },
  legendItem: { display: 'inline-flex', alignItems: 'center', gap: 4 },
  dot: { width: 10, height: 10, borderRadius: '50%', display: 'inline-block' },
  canvas: { flex: 1, minHeight: 300, background: '#fff' },
  loading: { position: 'absolute', top: 80, right: 20, padding: '4px 10px', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 4, fontSize: 12, color: '#6b7280' },
}
