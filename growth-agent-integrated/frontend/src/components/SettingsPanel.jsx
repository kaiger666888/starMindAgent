// 装帧面板 -- 顶栏「装帧」按钮点开，落纸式下拉卡
// 结构：区域书签 tab -> 字体（字样试写）/ 字号 / 纸色三行 -> 恢复默认
// 即时生效（无确定/取消按钮），Escape / 点外部关闭

import { useState, useEffect, useRef } from 'react'
import { ZONES, FONTS, SIZES, PAPERS, DEFAULT_PREF, savePrefs } from '../bindding'

export default function SettingsPanel({ prefs, onChange }) {
  const [open, setOpen] = useState(false)
  const [zone, setZone] = useState('reading') // 面板当前编辑的区域
  const rootRef = useRef(null)

  // Escape 关闭 / 点外部关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    const onClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  function setField(zoneKey, field, value) {
    const next = {
      ...prefs,
      [zoneKey]: { ...(prefs[zoneKey] || DEFAULT_PREF), [field]: value },
    }
    onChange(next)
    savePrefs(next)
  }

  function resetZone(zoneKey) {
    const next = { ...prefs }
    delete next[zoneKey]
    onChange(next)
    savePrefs(next)
  }

  const cur = prefs[zone] || DEFAULT_PREF
  const isCustom = prefs[zone] != null

  return (
    <div ref={rootRef} style={styles.root}>
      <button
        style={{ ...styles.trigger, ...(open ? styles.triggerOn : {}) }}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title="分区装帧：字体、字号、纸色"
      >
        {/* 书脊线 icon：三根不等长横线，衬线气质 */}
        <span style={styles.spineIcon} aria-hidden="true">
          <i style={{ ...styles.spineLine, width: 13 }} />
          <i style={{ ...styles.spineLine, width: 9 }} />
          <i style={{ ...styles.spineLine, width: 11 }} />
        </span>
        装帧
      </button>

      {open && (
        <div style={styles.panel} role="dialog" aria-label="分区装帧">
          <p style={styles.lead}>
            选一个区域，试它的字体、字号与纸色，改动即时落纸。全局纸色在阅读区右上角。
          </p>

          {/* 区域书签 tab */}
          <div style={styles.zoneTabs} role="tablist" aria-label="区域">
            {ZONES.map((z) => {
              const on = z.key === zone
              const tagged = prefs[z.key] != null
              return (
                <button
                  key={z.key}
                  role="tab"
                  aria-selected={on}
                  style={{ ...styles.zoneTab, ...(on ? styles.zoneTabOn : {}) }}
                  onClick={() => setZone(z.key)}
                >
                  {z.label}
                  {tagged && <span style={styles.zoneTag} title="已自定义" />}
                </button>
              )
            })}
          </div>

          {/* 字体：字样试写 -- 标签用该字体自身渲染 */}
          <div style={styles.row}>
            <span style={styles.rowLabel}>字体</span>
            <div style={styles.options}>
              {FONTS.map((f) => (
                <button
                  key={f.key}
                  style={{
                    ...styles.fontOpt,
                    fontFamily: f.stack,
                    ...(cur.font === f.key ? styles.optOn : {}),
                  }}
                  onClick={() => setField(zone, 'font', f.key)}
                  aria-pressed={cur.font === f.key}
                >
                  {f.label}
                  <span style={{ ...styles.fontHint, fontFamily: f.stack }}>{f.hint}</span>
                </button>
              ))}
            </div>
          </div>

          {/* 字号 */}
          <div style={styles.row}>
            <span style={styles.rowLabel}>字号</span>
            <div style={styles.options}>
              {SIZES.map((s) => (
                <button
                  key={s}
                  style={{ ...styles.opt, ...(cur.size === s ? styles.optOn : {}) }}
                  onClick={() => setField(zone, 'size', s)}
                  aria-pressed={cur.size === s}
                >
                  {s === '紧凑' ? 'A−' : s === '宽松' ? 'A+' : 'A'}
                  <span style={styles.optNote}>{s}</span>
                </button>
              ))}
            </div>
          </div>

          {/* 纸色 */}
          <div style={styles.row}>
            <span style={styles.rowLabel}>纸色</span>
            <div style={styles.options}>
              {PAPERS.map((p) => (
                <button
                  key={p}
                  style={{ ...styles.opt, ...(cur.paper === p ? styles.optOn : {}) }}
                  onClick={() => setField(zone, 'paper', p)}
                  aria-pressed={cur.paper === p}
                >
                  <span
                    style={{
                      ...styles.paperDot,
                      background:
                        p === '微沉'
                          ? 'color-mix(in srgb, var(--t-paper) 88%, #1F2421)'
                          : p === '微暖'
                            ? 'color-mix(in srgb, var(--t-paper) 86%, #C9A876)'
                            : 'var(--t-paper)',
                    }}
                  />
                  {p}
                </button>
              ))}
            </div>
          </div>

          {/* 底部：恢复默认 */}
          <div style={styles.footer}>
            <span style={styles.footerNote}>
              {isCustom ? '本区已另装帧' : '沿用全局装帧'}
            </span>
            <button
              style={styles.resetBtn}
              onClick={() => resetZone(zone)}
              disabled={!isCustom}
            >
              恢复默认
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

const styles = {
  root: { position: 'relative', display: 'flex' },
  trigger: {
    display: 'inline-flex', alignItems: 'center', gap: 7,
    padding: '5px 13px', fontSize: 13, border: 'none', borderRadius: 'var(--r-sm)',
    cursor: 'pointer', fontFamily: 'var(--sans)', color: 'var(--ink-soft)',
    background: 'transparent', transition: 'all 0.15s',
  },
  triggerOn: { background: 'var(--paper-soft)', color: 'var(--ink)' },
  spineIcon: { display: 'inline-flex', flexDirection: 'column', gap: 2.5, width: 13 },
  spineLine: { display: 'block', height: 1.5, background: 'currentColor', borderRadius: 1 },

  // 落纸面板：header 下右对齐，纸张质感
  panel: {
    position: 'absolute', top: 'calc(100% + 8px)', right: 0, zIndex: 60,
    width: 392, padding: '18px 18px 12px',
    background: 'var(--paper)', border: '1px solid var(--rule)',
    borderRadius: 'var(--r-lg)',
    boxShadow: '0 12px 32px rgba(31, 36, 33, 0.14), 0 2px 6px rgba(31, 36, 33, 0.08)',
    animation: 'paperDrop 0.22s cubic-bezier(0.2, 0.7, 0.3, 1)',
  },
  lead: {
    margin: '0 0 12px', fontSize: 11.5, lineHeight: 1.6,
    color: 'var(--ink-soft)', fontFamily: 'var(--sans)',
  },

  // 区域书签：选中的像插进纸里（底边融进分隔线）
  zoneTabs: {
    display: 'flex', gap: 4, borderBottom: '1px solid var(--rule)',
    marginBottom: 14, marginLeft: -4,
  },
  zoneTab: {
    position: 'relative', padding: '7px 14px 9px', border: 'none',
    background: 'transparent', cursor: 'pointer', borderRadius: 'var(--r-sm) var(--r-sm) 0 0',
    fontFamily: 'var(--serif)', fontSize: 13.5, color: 'var(--ink-soft)',
    transition: 'color 0.15s', display: 'inline-flex', alignItems: 'center', gap: 6,
  },
  zoneTabOn: {
    color: 'var(--active-ink)', fontWeight: 600,
    background: 'var(--active-soft)',
    // 书签插进分隔线：底边压过 hairline，视觉上「插在纸上」
    marginBottom: -1, borderBottom: '2px solid var(--active)',
  },
  zoneTag: {
    width: 4, height: 4, borderRadius: '50%', background: 'var(--settled)',
    flexShrink: 0,
  },

  row: { display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12 },
  rowLabel: {
    flexShrink: 0, width: 32, paddingTop: 8,
    fontSize: 11, color: 'var(--ink-faint)', fontFamily: 'var(--mono)',
    letterSpacing: '0.08em',
  },
  options: { display: 'flex', gap: 6, flexWrap: 'wrap' },

  opt: {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    padding: '6px 12px', fontSize: 12.5, border: '1px solid var(--rule-soft)',
    borderRadius: 'var(--r-sm)', background: 'var(--paper)',
    color: 'var(--ink-soft)', cursor: 'pointer', fontFamily: 'var(--sans)',
    transition: 'all 0.15s',
  },
  optOn: {
    borderColor: 'var(--active)', color: 'var(--active-ink)',
    background: 'var(--active-soft)', fontWeight: 500,
  },
  optNote: { fontSize: 10, color: 'var(--ink-faint)', fontFamily: 'var(--mono)' },

  // 字体选项：字样试写（label 即样本）
  fontOpt: {
    display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 1,
    minWidth: 64, padding: '7px 12px 6px', fontSize: 15,
    border: '1px solid var(--rule-soft)', borderRadius: 'var(--r-sm)',
    background: 'var(--paper)', color: 'var(--ink-soft)', cursor: 'pointer',
    transition: 'all 0.15s',
  },
  fontHint: { fontSize: 9.5, color: 'var(--ink-faint)', letterSpacing: '0.06em' },

  paperDot: {
    width: 13, height: 13, borderRadius: '50%', flexShrink: 0,
    boxShadow: 'inset 0 0 0 1px var(--rule)',
  },

  footer: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    paddingTop: 10, borderTop: '1px solid var(--rule-soft)', marginTop: 2,
  },
  footerNote: { fontSize: 11, color: 'var(--ink-faint)', fontFamily: 'var(--mono)' },
  resetBtn: {
    padding: '3px 10px', fontSize: 11.5, border: '1px solid var(--rule)',
    borderRadius: 'var(--r-sm)', background: 'transparent', cursor: 'pointer',
    color: 'var(--ink-soft)', fontFamily: 'var(--sans)', transition: 'all 0.15s',
  },
}
