import React, { useEffect, useRef } from 'react'

/* 「页边追问」对话框 -- 就选段/就这层提出针对性问题，问题长成探索树子层
 *
 * 气质定位（与设计系统一致，见 tokens.css）：
 * - 这是"阅读手账的便签"动作，不是"工具按钮"动作：
 *   暖纸底 paper-warm、发丝线框、衬线标题，与"标为概念"的墨黑气泡
 *   （工具感）形成气质区隔。
 * - 引用块（选段/层问题）用陶土棕左线 -- 沉淀色系，"基于已读内容追问"。
 * - Enter 发送 / Shift+Enter 换行 / Esc 关闭；打开即聚焦。
 */

export default function AskDialog({
  open,
  anchor,            // { kind: 'selection'|'layer', text, label } 追问对象
  submitting,
  onSubmit,          // (question: string) => void
  onClose,
}) {
  const [text, setText] = React.useState('')
  const inputRef = useRef(null)
  const lastFocusRef = useRef(null)

  // 打开时：清空旧文、聚焦输入框、记住来源焦点（关闭时归还）
  useEffect(() => {
    if (open) {
      setText('')
      lastFocusRef.current = document.activeElement
      // 等对话框挂载后再聚焦
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  // Esc 关闭（在输入框外按也生效）
  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  function submit() {
    const q = text.trim()
    if (!q || submitting) return
    onSubmit(q)
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const quote = anchor?.text || ''
  const quoteLabel = anchor?.kind === 'selection' ? '就选段追问' : '就这层追问'

  return (
    <div
      style={styles.overlay}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
      role="dialog"
      aria-modal="true"
      aria-label={quoteLabel}
    >
      <div style={styles.card}>
        <div style={styles.head}>
          <span style={styles.seal} aria-hidden="true">问</span>
          <span style={styles.title}>{quoteLabel}</span>
          <button style={styles.closeBtn} onClick={onClose} aria-label="关闭">✕</button>
        </div>

        {/* 追问对象引用：陶土棕左线，截断到 3 行 */}
        {quote && (
          <blockquote style={styles.quote}>
            <span style={styles.quoteMark} aria-hidden="true">「</span>
            <span style={styles.quoteText}>{quote}</span>
            <span style={styles.quoteMark} aria-hidden="true">」</span>
          </blockquote>
        )}

        <textarea
          ref={inputRef}
          style={styles.input}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="针对上面这段，你想问什么？"
          rows={3}
          disabled={submitting}
          aria-label="追问内容"
        />

        <div style={styles.foot}>
          <span style={styles.tip}>Enter 发送 · Shift+Enter 换行</span>
          <button
            style={{ ...styles.askBtn, ...(submitting ? styles.askBtnBusy : {}) }}
            onClick={submit}
            disabled={!text.trim() || submitting}
          >
            {submitting ? '生长中…' : '提问 · 长成子层'}
          </button>
        </div>
      </div>
    </div>
  )
}

const styles = {
  overlay: {
    position: 'fixed', inset: 0, zIndex: 100,
    background: 'rgba(31, 36, 33, 0.28)',
    display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
    // 出现在视口上 1/3（视线自然落点），不遮正文下半屏
    paddingTop: '18vh',
  },
  card: {
    width: 'min(520px, calc(100vw - 48px))',
    background: 'var(--paper-warm)',
    border: '1px solid var(--rule)',
    borderRadius: 'var(--r-md)',
    boxShadow: '0 12px 32px rgba(31, 36, 33, 0.18)',
    padding: '16px 18px 14px',
    animation: 'paperDrop 0.18s ease-out',
  },
  head: {
    display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
  },
  seal: {
    // 小印章：墨蓝描边方块内一个衬线"问"字，手账印章气质
    width: 20, height: 20, display: 'inline-flex',
    alignItems: 'center', justifyContent: 'center',
    border: '1px solid var(--active)', color: 'var(--active)',
    borderRadius: 'var(--r-sm)', fontFamily: 'var(--serif)',
    fontSize: 12, fontWeight: 600, flexShrink: 0,
  },
  title: {
    fontFamily: 'var(--serif)', fontSize: 14, fontWeight: 600,
    color: 'var(--ink)', letterSpacing: '0.02em', flex: 1,
  },
  closeBtn: {
    background: 'none', border: 'none', cursor: 'pointer',
    color: 'var(--ink-faint)', fontSize: 13, padding: '2px 4px',
    borderRadius: 'var(--r-sm)',
  },
  quote: {
    margin: '0 0 12px', padding: '6px 10px 6px 12px',
    borderLeft: '2px solid var(--settled)',
    background: 'var(--paper-soft)',
    borderRadius: '0 var(--r-sm) var(--r-sm) 0',
    fontFamily: 'var(--serif)', fontSize: 12.5, lineHeight: 1.6,
    color: 'var(--ink-soft)',
    display: '-webkit-box', WebkitLineClamp: 3,
    WebkitBoxOrient: 'vertical', overflow: 'hidden',
  },
  quoteMark: { color: 'var(--settled)', fontWeight: 600 },
  quoteText: { letterSpacing: '0.01em' },
  input: {
    width: '100%', resize: 'vertical', minHeight: 72,
    padding: '10px 12px', fontFamily: 'var(--serif)', fontSize: 14,
    lineHeight: 1.7, color: 'var(--ink-read)',
    background: 'var(--paper)', border: '1px solid var(--rule)',
    borderRadius: 'var(--r-sm)', outline: 'none',
    transition: 'border-color 0.15s',
  },
  foot: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    marginTop: 10,
  },
  tip: {
    fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-faint)',
    letterSpacing: '0.04em',
  },
  askBtn: {
    padding: '7px 16px', background: 'var(--active)', color: '#fff',
    border: 'none', borderRadius: 'var(--r-sm)', cursor: 'pointer',
    fontFamily: 'var(--sans)', fontSize: 12.5, fontWeight: 500,
    transition: 'opacity 0.15s',
  },
  askBtnBusy: { opacity: 0.6, cursor: 'progress' },
}
