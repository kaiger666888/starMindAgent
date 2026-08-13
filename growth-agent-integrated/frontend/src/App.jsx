import React, { useState, useEffect } from 'react'
import TreeView from './components/TreeView'
import ConceptGraph from './components/ConceptGraph'
import ReadingPane from './components/ReadingPane'
import MemoryView from './components/MemoryView'
import { useStore } from './store/qaStore'

export default function App() {
  const tree = useStore((s) => s.tree)
  const inflight = useStore((s) => s.inflight)
  const [view, setView] = useState('explore') // explore | memory

  // 概念图状态三导航：点灰色未探索概念 → 切到探索视图并预填问题
  useEffect(() => {
    const handler = (e) => {
      setView('explore')
      window.dispatchEvent(new CustomEvent('starmind:prefillQuestion', { detail: e.detail }))
    }
    window.addEventListener('starmind:startFromConcept', handler)
    return () => window.removeEventListener('starmind:startFromConcept', handler)
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
          </nav>
        </div>
      </header>
      <div style={styles.body}>
        {view === 'explore' ? (
          <>
            <TreeView />
            <main style={styles.main}>
              <ReadingPane />
            </main>
            <aside style={styles.graphAside}>
              <ConceptGraph />
            </aside>
          </>
        ) : (
          <main style={styles.memoryMain}>
            <MemoryView />
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
  inflight: {
    display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11,
    color: 'var(--active)', fontFamily: 'var(--mono)', letterSpacing: '0.04em',
  },
  pulse: {
    width: 6, height: 6, borderRadius: '50%', background: 'var(--active)',
    animation: 'pulse 1.4s ease-in-out infinite',
  },
  body: { flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 },
  main: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 },
  graphAside: { width: 380, borderLeft: '1px solid var(--rule)', display: 'flex', flexDirection: 'column', flexShrink: 0 },
  memoryMain: { flex: 1, overflow: 'auto', background: 'var(--paper)' },
}
