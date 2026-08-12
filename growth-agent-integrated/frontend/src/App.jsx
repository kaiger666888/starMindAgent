import React, { useState, useEffect } from 'react'
import TreeView from './components/TreeView'
import ConceptGraph from './components/ConceptGraph'
import MemoryView from './components/MemoryView'
import { useStore } from './store/qaStore'

export default function App() {
  const stack = useStore((s) => s.stack)
  const inflight = useStore((s) => s.inflight)
  const [view, setView] = useState('explore') // explore | memory

  // 概念图状态三导航：点灰色未探索概念 → 切到探索视图并预填问题
  useEffect(() => {
    const handler = (e) => {
      setView('explore')
      // 通过自定义事件传给 TreeView（TreeView 监听 window 预填）
      window.dispatchEvent(new CustomEvent('starmind:prefillQuestion', { detail: e.detail }))
    }
    window.addEventListener('starmind:startFromConcept', handler)
    return () => window.removeEventListener('starmind:startFromConcept', handler)
  }, [])

  return (
    <div style={styles.app}>
      <header style={styles.header}>
        <h1 style={styles.title}>伴你成长 · 概念探索</h1>
        <nav style={styles.nav}>
          <button
            style={{ ...styles.navBtn, ...(view === 'explore' ? styles.navBtnActive : {}) }}
            onClick={() => setView('explore')}
          >
            探索
          </button>
          <button
            style={{ ...styles.navBtn, ...(view === 'memory' ? styles.navBtnActive : {}) }}
            onClick={() => setView('memory')}
          >
            档案
          </button>
        </nav>
        {inflight && view === 'explore' && (
          <span style={styles.inflight}>生成中…（在途请求互斥，请稍候）</span>
        )}
      </header>
      <div style={styles.body}>
        {view === 'explore' ? (
          <>
            <TreeView />
            <main style={styles.main}>
              <ConceptGraph />
            </main>
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
  app: { height: '100%', display: 'flex', flexDirection: 'column' },
  header: {
    padding: '10px 16px', borderBottom: '1px solid #e5e7eb',
    display: 'flex', alignItems: 'center', gap: 16, background: '#fff',
  },
  title: { fontSize: 16, margin: 0, flex: 1 },
  nav: { display: 'flex', gap: 4 },
  navBtn: {
    padding: '5px 12px', fontSize: 13, background: 'transparent', color: '#6b7280',
    border: '1px solid #e5e7eb', borderRadius: 4, cursor: 'pointer',
  },
  navBtnActive: { background: '#2563eb', color: '#fff', borderColor: '#2563eb' },
  inflight: { fontSize: 12, color: '#f59e0b' },
  body: { flex: 1, display: 'flex', overflow: 'hidden' },
  main: { flex: 1, display: 'flex', flexDirection: 'column' },
  memoryMain: {
    flex: 1, overflow: 'auto', background: '#FAF8F3',
  },
}
