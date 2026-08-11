import React from 'react'
import TreeView from './components/TreeView'
import ConceptGraph from './components/ConceptGraph'
import { useStore } from './store/qaStore'

export default function App() {
  const stack = useStore((s) => s.stack)
  const inflight = useStore((s) => s.inflight)

  return (
    <div style={styles.app}>
      <header style={styles.header}>
        <h1 style={styles.title}>伴你成长 · 概念探索</h1>
        {inflight && <span style={styles.inflight}>生成中…（在途请求互斥，请稍候）</span>}
      </header>
      <div style={styles.body}>
        <TreeView />
        <main style={styles.main}>
          <ConceptGraph />
        </main>
      </div>
    </div>
  )
}

const styles = {
  app: { height: '100%', display: 'flex', flexDirection: 'column' },
  header: { padding: '10px 16px', borderBottom: '1px solid #e5e7eb', display: 'flex', alignItems: 'center', gap: 12, background: '#fff' },
  title: { fontSize: 16, margin: 0 },
  inflight: { fontSize: 12, color: '#f59e0b' },
  body: { flex: 1, display: 'flex', overflow: 'hidden' },
  main: { flex: 1, display: 'flex', flexDirection: 'column' },
}
