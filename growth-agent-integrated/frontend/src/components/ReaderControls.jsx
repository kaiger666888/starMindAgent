// 阅读区主题/字号切换器 —— 安静的工具条,默认低透明度 hover 浮现
// 4 套主题用小圆色块(各自纸/墨色)代表,字号 A-/A+ 三档
// 读写 localStorage,挂载时 apply 到 document.documentElement.dataset

import { useState, useEffect } from 'react'

const THEMES = [
  { key: '暖纸', dot: '#FAF8F3', ring: '#1F2421', label: '暖纸' },
  { key: '夜读', dot: '#1A1F26', ring: '#D4CBB8', label: '夜读' },
  { key: '护眼', dot: '#E8EBE3', ring: '#2A332B', label: '护眼' },
  { key: '墨', dot: '#0E0F0D', ring: '#E8E1D0', label: '墨' },
]
const SIZES = ['紧凑', '舒适', '宽松']
const DEFAULT_SIZE = '舒适'

function applyTheme(theme) {
  if (theme && theme !== '暖纸') document.documentElement.dataset.theme = theme
  else delete document.documentElement.dataset.theme
}
function applySize(size) {
  if (size && size !== DEFAULT_SIZE) document.documentElement.dataset.size = size
  else delete document.documentElement.dataset.size
}

export function initReaderPrefs() {
  // 挂载前同步应用(localStorage 可能已存上次选择)
  applyTheme(localStorage.getItem('starmind:theme'))
  applySize(localStorage.getItem('starmind:size'))
}

export default function ReaderControls() {
  const [theme, setTheme] = useState(() => localStorage.getItem('starmind:theme') || '暖纸')
  const [size, setSize] = useState(() => localStorage.getItem('starmind:size') || DEFAULT_SIZE)

  useEffect(() => { initReaderPrefs() }, [])

  function onTheme(t) {
    setTheme(t)
    if (t === '暖纸') { localStorage.removeItem('starmind:theme'); applyTheme(null) }
    else { localStorage.setItem('starmind:theme', t); applyTheme(t) }
  }
  function onSize(s) {
    setSize(s)
    if (s === DEFAULT_SIZE) { localStorage.removeItem('starmind:size'); applySize(null) }
    else { localStorage.setItem('starmind:size', s); applySize(s) }
  }

  return (
    <div className="readerControls" style={styles.wrap} role="toolbar" aria-label="阅读主题与字号">
      <div style={styles.group} aria-label="主题">
        {THEMES.map((t) => (
          <button
            key={t.key}
            style={{
              ...styles.themeBtn,
              borderColor: theme === t.key ? 'var(--active)' : 'transparent',
            }}
            onClick={() => onTheme(t.key)}
            title={t.label}
            aria-label={t.label}
            aria-pressed={theme === t.key}
          >
            <span style={{ ...styles.dot, background: t.dot, boxShadow: `inset 0 0 0 1px ${t.ring}` }} />
          </button>
        ))}
      </div>
      <span style={styles.sep} aria-hidden="true" />
      <div style={styles.group} aria-label="字号">
        {SIZES.map((s) => (
          <button
            key={s}
            style={{
              ...styles.sizeBtn,
              ...(size === s ? styles.sizeBtnActive : {}),
            }}
            onClick={() => onSize(s)}
            title={s}
            aria-pressed={size === s}
          >{s === '紧凑' ? 'A−' : s === '宽松' ? 'A+' : 'A'}</button>
        ))}
      </div>
    </div>
  )
}

const styles = {
  wrap: {
    position: 'absolute', top: 18, right: 12, zIndex: 5,
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '4px 8px', borderRadius: 'var(--r-pill)',
    background: 'var(--paper)', border: '1px solid var(--rule-soft)',
    opacity: 0.45, transition: 'opacity 0.25s',
  },
  // hover 浮现:用 CSS :hover 不可行(内联),改用 group hover 兜底——这里靠 wrap 自身 opacity
  group: { display: 'flex', alignItems: 'center', gap: 3 },
  themeBtn: {
    width: 18, height: 18, padding: 0, border: '2px solid transparent',
    borderRadius: '50%', background: 'transparent', cursor: 'pointer',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    transition: 'border-color 0.15s',
  },
  dot: { width: 12, height: 12, borderRadius: '50%' },
  sep: { width: 1, height: 14, background: 'var(--rule-soft)', flexShrink: 0 },
  sizeBtn: {
    width: 22, height: 18, padding: 0, border: 'none', background: 'transparent',
    color: 'var(--ink-soft)', cursor: 'pointer', fontFamily: 'var(--serif)',
    fontSize: 13, lineHeight: 1, borderRadius: 'var(--r-sm)',
    transition: 'background 0.15s, color 0.15s',
  },
  sizeBtnActive: { color: 'var(--active)', background: 'var(--active-soft)' },
}
