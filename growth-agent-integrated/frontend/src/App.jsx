import React, { useState, useEffect } from 'react'
import TreeView from './components/TreeView'
import ConceptGraph from './components/ConceptGraph'
import ReadingPane from './components/ReadingPane'
import MemoryView from './components/MemoryView'
import ReviewView from './components/ReviewView'
import SettingsPanel from './components/SettingsPanel'
import { useStore } from './store/qaStore'
import { loadPrefs, zoneStyle } from './bindding'

export default function App() {
  const tree = useStore((s) => s.tree)
  const inflight = useStore((s) => s.inflight)
  const [view, setView] = useState('explore') // explore | memory | review
  // 装帧：分区字体/字号/纸色（容器级 CSS 变量遮蔽，组件零改动）
  const [bindding, setBindding] = useState(() => loadPrefs())

  // 概念图状态三导航：点灰色未探索概念 → 切到探索视图并预填问题
  useEffect(() => {
    const handler = (e) => {
      setView('explore')
      window.dispatchEvent(new CustomEvent('starmind:prefillQuestion', { detail: e.detail }))
    }
    window.addEventListener('starmind:startFromConcept', handler)
    return () => window.removeEventListener('starmind:startFromConcept', handler)
  }, [])

  // 从档案「继续探索」恢复会话:store 已重建树,这里只负责切到探索视图
  useEffect(() => {
    const handler = () => setView('explore')
    window.addEventListener('starmind:resumeSession', handler)
    return () => window.removeEventListener('starmind:resumeSession', handler)
  }, [])

  return (
    <div style={styles.app}>
      <header style={styles.header}>
        <div style={styles.brand}>
          <h1 style={styles.title}>伴你成长</h1>
          <span style={styles.subtitle}>概念探索 · 学习手账</span>
        </div>
        <div style={styles.headerRight}>
          {inflight && view === 'explore' && (
            <span style={styles.inflight}>
              <span style={styles.pulse} /> 落笔中
            </span>
          )}
          <nav style={styles.nav} aria-label="主视图">
            <button
              style={{ ...styles.navBtn, ...(view === 'explore' ? styles.navBtnActive : styles.navBtnIdle) }}
              onClick={() => setView('explore')}
            >
              探索
            </button>
            <button
              style={{ ...styles.navBtn, ...(view === 'memory' ? styles.navBtnActiveSettled : styles.navBtnIdle) }}
              onClick={() => setView('memory')}
            >
              档案
            </button>
            <button
              style={{ ...styles.navBtn, ...(view === 'review' ? styles.navBtnActiveReview : styles.navBtnIdle) }}
              onClick={() => setView('review')}
            >
              复习
            </button>
          </nav>
          <SettingsPanel prefs={bindding} onChange={setBindding} />
        </div>
      </header>
      <div style={styles.body}>
        {view === 'explore' ? (
          <>
            <div data-zone="tree" style={{ ...styles.treeZone, ...zoneStyle('tree', bindding.tree) }}>
              <TreeView />
            </div>
            <main data-zone="reading" style={{ ...styles.main, ...zoneStyle('reading', bindding.reading) }}>
              <ReadingPane />
            </main>
            <aside data-zone="graph" style={{ ...styles.graphAside, ...zoneStyle('graph', bindding.graph) }}>
              <ConceptGraph />
            </aside>
          </>
        ) : view === 'memory' ? (
          <main data-zone="memory" style={{ ...styles.memoryMain, ...zoneStyle('memory', bindding.memory) }}>
            <MemoryView />
          </main>
        ) : (
          <main data-zone="review" style={styles.memoryMain}>
            <ReviewView />
          </main>
        )}
      </div>
    </div>
  )
}

const styles = {
  app: { height: '100%', display: 'flex', flexDirection: 'column', background: 'var(--paper)' },
  header: {
    padding: '12px 24px', borderBottom: '1px solid var(--rule)',
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16,
    background: 'var(--paper)', flexShrink: 0,
  },
  brand: { display: 'flex', alignItems: 'baseline', gap: 10 },
  title: {
    fontFamily: 'var(--serif)', fontSize: 21, fontWeight: 600, margin: 0,
    letterSpacing: '0.03em', color: 'var(--ink)',
  },
  subtitle: {
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-faint)',
    textTransform: 'uppercase', letterSpacing: '0.12em',
  },
  headerRight: { display: 'flex', alignItems: 'center', gap: 16 },
  nav: { display: 'flex', gap: 2, background: 'var(--paper-soft)', borderRadius: 'var(--r-sm)', padding: 2 },
  navBtn: {
    padding: '5px 16px', fontSize: 13, border: 'none', borderRadius: 'var(--r-sm)', cursor: 'pointer',
    fontFamily: 'var(--sans)', transition: 'all 0.15s',
  },
  navBtnIdle: { background: 'transparent', color: 'var(--ink-soft)' },
  navBtnActive: { background: 'var(--active)', color: '#fff', fontWeight: 500 },
  navBtnActiveSettled: { background: 'var(--settled)', color: '#fff', fontWeight: 500 },
  navBtnActiveReview: { background: 'var(--active-ink)', color: '#fff', fontWeight: 500 },
  inflight: {
    display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11,
    color: 'var(--active)', fontFamily: 'var(--mono)', letterSpacing: '0.04em',
  },
  pulse: {
    width: 6, height: 6, borderRadius: '50%', background: 'var(--active)',
    animation: 'pulse 1.4s ease-in-out infinite',
  },
  body: { flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 },
  treeZone: { display: 'flex', flexDirection: 'column', flexShrink: 0 },
  main: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 },
  graphAside: { width: 380, borderLeft: '1px solid var(--rule)', display: 'flex', flexDirection: 'column', flexShrink: 0 },
  memoryMain: { flex: 1, overflow: 'auto', background: 'var(--paper)' },
}
