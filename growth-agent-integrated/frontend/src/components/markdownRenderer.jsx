// 极简 markdown 渲染器 —— 「伴你成长」阅读区专用
// 不引外部库,纯函数 (answer, concepts, renderConcept) -> ReactNode[]
//
// 支持语法:
//   块级: #/##/### 标题、围栏 ```代码块```、> 引用、-/* 无序列表、1. 有序列表、空行分段
//   内联: **bold**、*italic*、`code`、[text](url)
//   概念: 每个文本片段再跑概念切分,概念位置渲染为可下钻 chip(renderConcept 回调)
//
// 与 ReadingPane.buildInlineSegments 协同:概念匹配逻辑复用,保证下钻不丢。

import React from 'react'
import katex from 'katex'
import 'katex/dist/katex.min.css'

// -- LaTeX 公式渲染（KaTeX）--
// LLM 讲 ML 概念常吐 $Softmax(QK^T/\sqrt{d})V$ 这类公式,无公式支持时
// 原始 LaTeX 字面裸奔(\sqrt ^ {} 直显)且公式内 ** 会被误吃成粗体。
// 块级 $$...$$ 展示模式,内联 $...$ 行内模式。
// 误判防护:内联 $...$ 内必须含 LaTeX 特征字符(^ \ { } _ =)才按公式
// 渲染,否则是金额($100)按普通文本;未闭合 $ 也按文本(流式半截公式)。

const LATEX_HINT = /[\^{}_=]/  // 判定"这像 LaTeX"的特征字符

function renderKatex(tex, displayMode) {
  try {
    const html = katex.renderToString(tex, {
      displayMode, throwOnError: false,
      strict: 'ignore', trust: false,
    })
    return <span style={displayMode ? mdStyles.formulaBlock : mdStyles.formulaInline}
      dangerouslySetInnerHTML={{ __html: html }} />
  } catch {
    // 解析失败(流式半截公式等):显示原文,等后续 token 到达再重渲染
    return <span style={mdStyles.formulaRaw}>{tex}</span>
  }
}

// 提取行内 $...$ 段:返回 [{type:'tex', tex} | {type:'text', text}]
function splitInlineMath(text) {
  const out = []
  let pos = 0
  while (pos < text.length) {
    const d = text.indexOf('$', pos)
    if (d < 0) break
    const close = text.indexOf('$', d + 1)
    if (close < 0) break  // 未闭合(流式中):剩余全部按文本
    const inner = text.slice(d + 1, close)
    // 误判防护:空串或无 LaTeX 特征字符(如金额 $100)不按公式
    if (inner && LATEX_HINT.test(inner)) {
      if (d > pos) out.push({ type: 'text', text: text.slice(pos, d) })
      out.push({ type: 'tex', tex: inner })
      pos = close + 1
      continue
    }
    pos = d + 1  // 不是公式,$ 按普通文本跳过
  }
  if (pos < text.length) out.push({ type: 'text', text: text.slice(pos) })
  return out
}

// —— 把一段文本按概念首次出现位置切分(与 ReadingPane 同逻辑) ——
function splitByConcepts(text, concepts) {
  if (!concepts || concepts.length === 0 || !text) {
    return [{ type: 'text', text }]
  }
  const matches = []
  for (const c of concepts) {
    const names = [c.canonical_name, ...(c.aliases || [])].filter(Boolean)
    let earliest = -1, matchedName = null
    for (const n of names) {
      const idx = text.indexOf(n)
      if (idx >= 0 && (earliest < 0 || idx < earliest)) {
        earliest = idx
        matchedName = n
      }
    }
    if (earliest >= 0) matches.push({ concept: c, start: earliest, end: earliest + matchedName.length })
  }
  matches.sort((a, b) => a.start - b.start)
  const valid = []
  let lastEnd = 0
  for (const m of matches) {
    if (m.start >= lastEnd) { valid.push(m); lastEnd = m.end }
  }
  const segs = []
  let pos = 0
  for (const m of valid) {
    if (m.start > pos) segs.push({ type: 'text', text: text.slice(pos, m.start) })
    segs.push({ type: 'concept', concept: m.concept })
    pos = m.end
  }
  if (pos < text.length) segs.push({ type: 'text', text: text.slice(pos) })
  return segs
}

// —— 内联解析:把一行文本切成 bold/italic/code/link 殇记,再各自做概念切分 ——
// 用一个扫描器,按 ** * ` [ 依次匹配。简单可靠,覆盖 LLM 常见输出。
function parseInline(text, concepts, renderConcept, keyBase) {
  const nodes = []
  let i = 0
  let k = 0
  const pushText = (t) => {
    if (!t) return
    // 先按 $...$ 切公式(公式内不做概念切分,概念名不在公式里):
    // $Softmax(QK^T/\sqrt{d})V$ 类内容必须整体交给 KaTeX
    for (const mseg of splitInlineMath(t)) {
      if (mseg.type === 'tex') {
        nodes.push(<React.Fragment key={`${keyBase}-x${k++}`}>{renderKatex(mseg.tex, false)}</React.Fragment>)
        continue
      }
      // 非公式文本再按概念切分
      const segs = splitByConcepts(mseg.text, concepts)
      for (const seg of segs) {
        if (seg.type === 'text') {
          nodes.push(<React.Fragment key={`${keyBase}-t${k++}`}>{seg.text}</React.Fragment>)
        } else {
          nodes.push(<React.Fragment key={`${keyBase}-c${k++}`}>{renderConcept(seg.concept)}</React.Fragment>)
        }
      }
    }
  }

  while (i < text.length) {
    // bold **...**
    if (text[i] === '*' && text[i + 1] === '*') {
      const end = text.indexOf('**', i + 2)
      if (end > 0) {
        pushText(text.slice(0, i))
        nodes.push(<strong key={`${keyBase}-b${k++}`} style={mdStyles.bold}>{parseInline(text.slice(i + 2, end), concepts, renderConcept, `${keyBase}-bi${k}`)}</strong>)
        text = text.slice(end + 2); i = 0; continue
      }
    }
    // italic *...*  (避开 **)
    if (text[i] === '*' && text[i + 1] !== '*') {
      const end = text.indexOf('*', i + 1)
      if (end > i + 1) {
        pushText(text.slice(0, i))
        nodes.push(<em key={`${keyBase}-i${k++}`} style={mdStyles.italic}>{parseInline(text.slice(i + 1, end), concepts, renderConcept, `${keyBase}-ii${k}`)}</em>)
        text = text.slice(end + 1); i = 0; continue
      }
    }
    // inline code `...`
    if (text[i] === '`') {
      const end = text.indexOf('`', i + 1)
      if (end > i + 1) {
        pushText(text.slice(0, i))
        nodes.push(<code key={`${keyBase}-ic${k++}`} style={mdStyles.inlineCode}>{text.slice(i + 1, end)}</code>)
        text = text.slice(end + 1); i = 0; continue
      }
    }
    // link [text](url)
    if (text[i] === '[') {
      const close = text.indexOf(']', i + 1)
      if (close > i && text[close + 1] === '(') {
        const end = text.indexOf(')', close + 2)
        if (end > close) {
          pushText(text.slice(0, i))
          const label = text.slice(i + 1, close)
          const url = text.slice(close + 2, end)
          nodes.push(<a key={`${keyBase}-l${k++}`} href={url} target="_blank" rel="noopener noreferrer" style={mdStyles.link}>{label}</a>)
          text = text.slice(end + 1); i = 0; continue
        }
      }
    }
    i++
  }
  pushText(text.slice(0))
  return nodes
}

// —— 块级解析:按行扫描 ——
export function renderMarkdown(answer, concepts = [], renderConcept = () => null) {
  if (!answer) return []
  const lines = answer.replace(/\r\n/g, '\n').split('\n')
  const blocks = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const line = lines[i]

    // 块级公式 $$...$$（展示模式，居中排版）
    // 支持三种形态：单行 $$..$$、$$ 独占一行开 + 多行 + $$ 独占行闭
    if (line.trimStart().startsWith('$$')) {
      const single = line.trim().match(/^\$\$(.+)\$\$$/)
      if (single) {
        blocks.push(<div key={key++} style={mdStyles.formulaWrap}>{renderKatex(single[1], true)}</div>)
        i++
        continue
      }
      // 多行块公式：$$ 开行，收集到闭合 $$ 行
      const tex = []
      i++
      while (i < lines.length && !lines[i].trimStart().startsWith('$$')) {
        tex.push(lines[i]); i++
      }
      i++ // 跳过闭合 $$
      blocks.push(<div key={key++} style={mdStyles.formulaWrap}>{renderKatex(tex.join('\n'), true)}</div>)
      continue
    }

    // 围栏代码块 ```
    if (line.trimStart().startsWith('```')) {
      const lang = line.trim().slice(3).trim()
      const code = []
      i++
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
        code.push(lines[i]); i++
      }
      i++ // 跳过闭合 ```
      blocks.push(
        <pre key={key++} style={mdStyles.codeBlock}>
          {lang && <span style={mdStyles.codeLang}>{lang}</span>}
          <code style={mdStyles.codeText}>{code.join('\n')}</code>
        </pre>
      )
      continue
    }

    // 标题 # ## ###
    const h = line.match(/^(#{1,3})\s+(.*)$/)
    if (h) {
      const level = h[1].length
      const Tag = ['h3', 'h2', 'h1'][level - 1] || 'h3'
      const style = [mdStyles.h3, mdStyles.h2, mdStyles.h1][level - 1] || mdStyles.h3
      blocks.push(React.createElement(Tag, { key: key++, style },
        parseInline(h[2], concepts, renderConcept, `h${key}`)
      ))
      i++
      continue
    }

    // 引用 >
    if (line.trimStart().startsWith('>')) {
      const quote = []
      while (i < lines.length && lines[i].trimStart().startsWith('>')) {
        quote.push(lines[i].replace(/^\s*>\s?/, ''))
        i++
      }
      blocks.push(
        <blockquote key={key++} style={mdStyles.quote}>
          <span aria-hidden="true" style={mdStyles.quoteMark}>"</span>
          {parseInline(quote.join('\n'), concepts, renderConcept, `q${key}`)}
        </blockquote>
      )
      continue
    }

    // 无序列表 - * +
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''))
        i++
      }
      blocks.push(
        <ul key={key++} style={mdStyles.ul}>
          {items.map((it, j) => (
            <li key={j} style={mdStyles.li}>
              <span aria-hidden="true" style={mdStyles.ulMarker} />
              {parseInline(it, concepts, renderConcept, `ul${key}-${j}`)}
            </li>
          ))}
        </ul>
      )
      continue
    }

    // 有序列表 1.
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''))
        i++
      }
      blocks.push(
        <ol key={key++} style={mdStyles.ol}>
          {items.map((it, j) => (
            <li key={j} style={mdStyles.li}>
              <span aria-hidden="true" style={mdStyles.olMarker}>{j + 1}.</span>
              {parseInline(it, concepts, renderConcept, `ol${key}-${j}`)}
            </li>
          ))}
        </ol>
      )
      continue
    }

    // 分隔线 --- 或 ***
    if (/^\s*([-*])\1\1+\s*$/.test(line)) {
      blocks.push(<hr key={key++} style={mdStyles.hr} />)
      i++
      continue
    }

    // 表格 | a | b |
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?[-:| ]+\|?\s*$/.test(lines[i + 1])) {
      const header = splitTableRow(line)
      i += 2 // 跳过分隔行
      const rows = []
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(splitTableRow(lines[i]))
        i++
      }
      blocks.push(
        <table key={key++} style={mdStyles.table}>
          <thead><tr>{header.map((c, j) => <th key={j} style={mdStyles.th}>{parseInline(c, concepts, renderConcept, `th${key}-${j}`)}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>{r.map((c, ci) => <td key={ci} style={mdStyles.td}>{parseInline(c, concepts, renderConcept, `td${key}-${ri}-${ci}`)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      )
      continue
    }

    // 空行:段落分隔
    if (line.trim() === '') { i++; continue }

    // 普通段落:连续非空行合并
    const para = []
    while (i < lines.length && lines[i].trim() !== '' &&
      !/^(#{1,3})\s/.test(lines[i]) &&
      !lines[i].trimStart().startsWith('```') &&
      !lines[i].trimStart().startsWith('$$') &&
      !lines[i].trimStart().startsWith('>') &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^\s*\|.*\|\s*$/.test(lines[i]) &&
      !/^\s*([-*])\1\1+\s*$/.test(lines[i])) {
      para.push(lines[i]); i++
    }
    blocks.push(
      <p key={key++} style={mdStyles.p}>
        {parseInline(para.join('\n'), concepts, renderConcept, `p${key}`)}
      </p>
    )
  }
  return blocks
}

// 表格行切分: | a | b | → ['a', 'b']
function splitTableRow(line) {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
}

const mdStyles = {
  // 标题:衬线 + 底部发丝线(结构感),h1 字距加宽
  h1: {
    fontFamily: 'var(--serif)', fontSize: 'calc(var(--fs-body) + 6px)', fontWeight: 600,
    color: 'var(--ink)', margin: '28px 0 14px', lineHeight: 1.35, letterSpacing: '0.02em',
    paddingBottom: '6px', borderBottom: '1px solid var(--rule-soft)',
  },
  h2: {
    fontFamily: 'var(--serif)', fontSize: 'calc(var(--fs-body) + 3px)', fontWeight: 600,
    color: 'var(--ink)', margin: '22px 0 10px', lineHeight: 1.35,
  },
  h3: {
    fontFamily: 'var(--serif)', fontSize: 'var(--fs-body)', fontWeight: 600,
    color: 'var(--ink)', margin: '18px 0 8px', lineHeight: 1.4,
  },
  // 段落:舒展间距,衬线正文
  p: {
    margin: '0 0 16px', color: 'var(--ink-read)', lineHeight: 'var(--lh-body)',
    letterSpacing: 'var(--tracking-body)',
  },
  // 列表:marker 用墨蓝/陶土棕,li 间距呼吸
  ul: {
    margin: '0 0 16px', paddingLeft: '0', listStyle: 'none',
    color: 'var(--ink-read)', lineHeight: 'var(--lh-body)',
  },
  ol: {
    margin: '0 0 16px', paddingLeft: '0', listStyle: 'none',
    color: 'var(--ink-read)', lineHeight: 'var(--lh-body)', counterReset: 'md-ol',
  },
  li: {
    margin: '0 0 7px', paddingLeft: '26px', position: 'relative',
  },
  // ul marker:墨蓝小圆点
  ulMarker: {
    position: 'absolute', left: '8px', top: '0.55em', width: '5px', height: '5px',
    borderRadius: '50%', background: 'var(--active)', opacity: 0.7,
  },
  // ol marker:mono 数字 + 陶土棕
  olMarker: {
    position: 'absolute', left: 0, top: 0, fontFamily: 'var(--mono)', fontSize: '0.85em',
    color: 'var(--settled)', fontWeight: 500, minWidth: '20px',
  },
  bold: { fontWeight: 600, color: 'var(--ink)' },
  italic: { fontStyle: 'italic' },
  // inline code:mono + 浅底 + 圆角,不抢正文
  inlineCode: {
    fontFamily: 'var(--mono)', fontSize: '0.86em', background: 'var(--code-bg)',
    color: 'var(--code-ink)', padding: '1.5px 6px', borderRadius: 'var(--r-sm)',
    borderWidth: '1px', borderStyle: 'solid', borderColor: 'var(--rule-soft)',
  },
  // 代码块:左侧色带 + 右上语言标签胶囊 + mono 正文
  codeBlock: {
    margin: '0 0 18px', padding: '14px 16px 14px 18px', background: 'var(--code-bg)',
    borderRadius: 'var(--r-md)', overflowX: 'auto', position: 'relative',
    borderLeft: '3px solid var(--settled)',
  },
  codeLang: {
    position: 'absolute', top: 8, right: 10, fontFamily: 'var(--mono)', fontSize: 9.5,
    color: 'var(--ink-faint)', letterSpacing: '0.08em', textTransform: 'uppercase',
    background: 'var(--paper)', padding: '1px 6px', borderRadius: 'var(--r-sm)',
    border: '1px solid var(--rule-soft)',
  },
  codeText: {
    fontFamily: 'var(--mono)', fontSize: '0.88em', lineHeight: 1.65,
    color: 'var(--code-ink)', whiteSpace: 'pre',
  },
  // 引用:前引号 + 陶土棕色调,衬线斜体
  quote: {
    margin: '0 0 18px', padding: '8px 18px 8px 20px', background: 'var(--quote-bg)',
    borderLeft: '3px solid var(--settled)', borderRadius: 'var(--r-sm)',
    color: 'var(--ink-soft)', fontStyle: 'italic', fontFamily: 'var(--serif)',
    lineHeight: 'var(--lh-body)', position: 'relative',
  },
  quoteMark: {
    position: 'absolute', left: '6px', top: '-2px', fontFamily: 'var(--serif)',
    fontSize: '28px', color: 'var(--settled)', opacity: 0.35, lineHeight: 1,
  },
  link: {
    color: 'var(--active)', textDecoration: 'underline', textDecorationStyle: 'dotted',
    textUnderlineOffset: '3px',
  },
  // 表格:发丝线分隔,表头加底
  table: {
    margin: '0 0 18px', borderCollapse: 'collapse', width: '100%',
    fontSize: '0.95em', fontFamily: 'var(--serif)',
  },
  th: {
    padding: '8px 12px', borderBottom: '2px solid var(--rule)', color: 'var(--ink)',
    fontWeight: 600, textAlign: 'left', background: 'var(--paper-soft)',
  },
  td: {
    padding: '7px 12px', borderBottom: '1px solid var(--rule-soft)', color: 'var(--ink-read)',
  },
  // 公式:块级居中留白,内联随文,raw 是解析失败的原文回退
  formulaWrap: {
    margin: '6px 0 18px', textAlign: 'center', overflowX: 'auto',
    padding: '4px 0',
  },
  formulaInline: { display: 'inline-block', verticalAlign: 'baseline' },
  formulaRaw: {
    fontFamily: 'var(--mono)', fontSize: '0.9em', color: 'var(--ink-soft)',
    background: 'var(--code-bg)', padding: '1px 5px', borderRadius: 'var(--r-sm)',
  },
  // 分隔线 ---
  hr: {
    border: 'none', borderTop: '1px solid var(--rule)', margin: '20px 0',
  },
}
