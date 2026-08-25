// 装帧（分区排版定制）-- 每个区域可独立配置字体 / 字号 / 纸色
//
// 原理：CSS 自定义属性沿 DOM 继承，容器上重新声明同名变量即可遮蔽 :root 值，
// 子树内所有 var(--serif) / var(--paper) 引用取到的就是区域值。
// 既有组件零改动（它们只认业务变量），tokens.css 的 [data-theme] 主题层不受影响：
// 全局主题管「选哪张纸」，分区装帧管「这张纸上各区怎么排」。

// -- 字体档（每档标签在面板里用该字体自身渲染：字样试写） --
export const FONTS = [
  {
    key: 'serif', label: '衬线', hint: '书卷',
    stack: '"Source Serif 4", "Source Serif Pro", "Songti SC", "STSong", "Source Han Serif SC", "Noto Serif CJK SC", Georgia, serif',
  },
  {
    key: 'sans', label: '黑体', hint: '利落',
    stack: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei", "Noto Sans SC", sans-serif',
  },
  {
    key: 'kai', label: '楷体', hint: '手写',
    stack: '"Kaiti SC", "STKaiti", "KaiTi", "TW-Kai", "DFKai-SB", "Source Han Serif SC", serif',
  },
  {
    key: 'mono', label: '等宽', hint: '工整',
    stack: '"JetBrains Mono", "Cascadia Code", Consolas, "Microsoft YaHei", monospace',
  },
]

// -- 字号档（词汇与全局 ReaderControls 一致：紧凑/舒适/宽松） --
export const SIZES = ['紧凑', '舒适', '宽松']

const SIZE_VARS = {
  '紧凑': { '--t-fs-body': '14px', '--t-lh-body': '1.75', '--t-fs-h1': '17px', '--t-fs-h2': '14px' },
  '舒适': null, // = :root 默认，不覆盖
  '宽松': { '--t-fs-body': '16.5px', '--t-lh-body': '2.05', '--t-fs-h1': '20px', '--t-fs-h2': '16.5px' },
}

// -- 纸色档：主题内相对派生（color-mix），任何全局主题下文字对比度安全 --
// 微沉 = 往墨里压 5%；微暖 = 往暖色偏 6%。不提供自由取色，保护双信号色系统。
export const PAPERS = ['跟随', '微沉', '微暖']

const PAPER_VARS = {
  '跟随': null,
  '微沉': {
    '--paper': 'color-mix(in srgb, var(--t-paper) 95%, #1F2421)',
    '--paper-soft': 'color-mix(in srgb, var(--t-paper-soft) 95%, #1F2421)',
  },
  '微暖': {
    '--paper': 'color-mix(in srgb, var(--t-paper) 94%, #C9A876)',
    '--paper-soft': 'color-mix(in srgb, var(--t-paper-soft) 94%, #C9A876)',
  },
}

// -- 区域：顶栏是框架 chrome 不参与（导航稳定性） --
export const ZONES = [
  { key: 'reading', label: '正文' },
  { key: 'tree', label: '侧栏' },
  { key: 'graph', label: '图谱' },
  { key: 'memory', label: '档案' },
]

export const DEFAULT_PREF = { font: 'serif', size: '舒适', paper: '跟随' }
const STORE_KEY = 'starmind:bindding'

export function loadPrefs() {
  try {
    const raw = localStorage.getItem(STORE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    // 逐区校验，坏值静默丢弃
    const clean = {}
    for (const z of ZONES) {
      const p = parsed[z.key]
      if (!p) continue
      clean[z.key] = {
        font: FONTS.some((f) => f.key === p.font) ? p.font : DEFAULT_PREF.font,
        size: SIZES.includes(p.size) ? p.size : DEFAULT_PREF.size,
        paper: PAPERS.includes(p.paper) ? p.paper : DEFAULT_PREF.paper,
      }
    }
    return clean
  } catch {
    return {}
  }
}

export function savePrefs(prefs) {
  try {
    const hasCustom = Object.values(prefs).some(
      (p) => p.font !== DEFAULT_PREF.font || p.size !== DEFAULT_PREF.size || p.paper !== DEFAULT_PREF.paper,
    )
    if (hasCustom) localStorage.setItem(STORE_KEY, JSON.stringify(prefs))
    else localStorage.removeItem(STORE_KEY)
  } catch {
    /* 隐私模式等存储失败：本次会话内仍生效，只是不持久化 */
  }
}

/**
 * 生成某区域的容器级 CSS 变量覆盖（合并进容器 style）。
 * 字体：所选字体接管该区的 --serif 与 --sans（层级靠字号字重承担，纯黑体排版
 * 的日式书籍做法）；等宽档连 --mono 一起接管。代码位 --mono 其余档不动。
 */
export function zoneStyle(zone, pref) {
  if (!pref) return {}
  const vars = {}
  const font = FONTS.find((f) => f.key === pref.font)
  if (font && font.key !== 'serif') {
    vars['--serif'] = font.stack
    vars['--sans'] = font.stack
    if (font.key === 'mono') vars['--mono'] = font.stack
  }
  Object.assign(vars, SIZE_VARS[pref.size] || {})
  Object.assign(vars, PAPER_VARS[pref.paper] || {})
  return vars
}
