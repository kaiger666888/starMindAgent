import React, { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import { useStore } from '../store/qaStore'
import { getGraph, drillDown } from '../api/client'

// 三状态着色：origin -> 边颜色；node 热度 -> 填充
const ORIGIN_STYLE = {
  user_click: { color: '#2563eb', label: '状态一·用户点击下钻' },
  co_occurrence: { color: '#9333ea', label: '状态二·同次抽取共现' },
  domain_graph: { color: '#64748b', label: '状态三·领域预置图' },
}
// 节点热度色彩（与后端 _color_tier 对齐）
const TIER_FILL = {
  gray: '#9ca3af', green: '#22c55e',
  red_1: '#fca5a5', red_2: '#f87171', red_3: '#ef4444', red_4: '#b91c1c',
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

  useEffect(() => {
    if (!activeSid) return
    let cancelled = false
    getGraph(activeSid).then((g) => {
      if (cancelled || !cyRef.current) return
      render(cyRef.current, g)
    })
    return () => { cancelled = true }
  }, [activeSid, stack.length])

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
      if (qaId) drillDown(qaId, n.id(), n.data('label'))
    })
    cyRef.current = cy
    return () => cy.destroy()
  }, [])

  function render(cy, g) {
    cy.elements().remove()
    const nodes = (g.nodes || []).map((n) => ({
      data: {
        id: n.concept_id, label: n.canonical_name,
        explore_count: n.explore_count,
      },
      style: { 'background-color': TIER_FILL[tierForCount(n.explore_count || 0)] },
    }))
    const edges = (g.edges || []).map((e) => ({
      data: { id: e.edge_id, source: e.source_id, target: e.target_id, origin: e.origin },
      style: { 'line-color': ORIGIN_STYLE[e.origin]?.color || '#cbd5e1',
               'target-arrow-color': ORIGIN_STYLE[e.origin]?.color || '#cbd5e1' },
    }))
    cy.add([...nodes, ...edges])
    cy.layout({ name: 'cose', animate: true }).run()
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
      <div style={styles.legend}>
        {Object.entries(ORIGIN_STYLE).map(([k, v]) => (
          <span key={k} style={styles.legendItem}>
            <i style={{ ...styles.dot, background: v.color }} /> {v.label}
          </span>
        ))}
      </div>
      <div ref={containerRef} style={styles.canvas} />
    </div>
  )
}

const styles = {
  legend: { display: 'flex', gap: 16, padding: '8px 12px', fontSize: 12, color: '#475569', borderBottom: '1px solid #e5e7eb' },
  legendItem: { display: 'inline-flex', alignItems: 'center', gap: 4 },
  dot: { width: 10, height: 10, borderRadius: '50%', display: 'inline-block' },
  canvas: { flex: 1, minHeight: 360, background: '#fff' },
}
