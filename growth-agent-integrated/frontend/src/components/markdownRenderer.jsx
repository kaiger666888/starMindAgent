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
    // 这段文本再按概念切分
    const segs = splitByConcepts(t, concepts)
    for (const seg of segs) {
      if (seg.type === 'text') {
        nodes.push(<React.Fragment key={`${keyBase}-t${k++}`}>{seg.text}</React.Fragment>)
      } else {
        nodes.push(<React.Fragment key={`${keyBase}-c${k++}`}>{renderConcept(seg.concept)}</React.Fragment>)
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
          <code>{code.join('\n')}</code>
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
            <li key={j} style={mdStyles.li}>{parseInline(it, concepts, renderConcept, `ul${key}-${j}`)}</li>
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
            <li key={j} style={mdStyles.li}>{parseInline(it, concepts, renderConcept, `ol${key}-${j}`)}</li>
          ))}
        </ol>
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
      !lines[i].trimStart().startsWith('>') &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])) {
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

const mdStyles = {
  h1: { fontFamily: 'var(--serif)', fontSize: 'calc(var(--fs-body) + 5px)', fontWeight: 600, color: 'var(--ink)', margin: '24px 0 12px', lineHeight: 1.4, letterSpacing: '0.01em' },
  h2: { fontFamily: 'var(--serif)', fontSize: 'calc(var(--fs-body) + 2px)', fontWeight: 600, color: 'var(--ink)', margin: '20px 0 10px', lineHeight: 1.4 },
  h3: { fontFamily: 'var(--serif)', fontSize: 'var(--fs-body)', fontWeight: 600, color: 'var(--ink)', margin: '16px 0 8px', lineHeight: 1.4 },
  p: { margin: '0 0 14px', color: 'var(--ink-read)', lineHeight: 'var(--lh-body)', letterSpacing: 'var(--tracking-body)' },
  ul: { margin: '0 0 14px', paddingLeft: '22px', color: 'var(--ink-read)', lineHeight: 'var(--lh-body)' },
  ol: { margin: '0 0 14px', paddingLeft: '22px', color: 'var(--ink-read)', lineHeight: 'var(--lh-body)' },
  li: { margin: '0 0 4px' },
  bold: { fontWeight: 600, color: 'var(--ink)' },
  italic: { fontStyle: 'italic' },
  inlineCode: {
    fontFamily: 'var(--mono)', fontSize: '0.88em', background: 'var(--code-bg)',
    color: 'var(--code-ink)', padding: '1px 5px', borderRadius: 'var(--r-sm)',
    borderWidth: '1px', borderStyle: 'solid', borderColor: 'var(--rule-soft)',
  },
  codeBlock: {
    margin: '0 0 16px', padding: '14px 16px', background: 'var(--code-bg)',
    borderRadius: 'var(--r-md)', overflowX: 'auto',
    borderLeft: '3px solid var(--quote-border)', position: 'relative',
  },
  codeLang: {
    position: 'absolute', top: 6, right: 10, fontFamily: 'var(--mono)',
    fontSize: 10, color: 'var(--ink-faint)', letterSpacing: '0.06em',
  },
  quote: {
    margin: '0 0 16px', padding: '10px 16px', background: 'var(--quote-bg)',
    borderLeft: '3px solid var(--quote-border)', borderRadius: 'var(--r-sm)',
    color: 'var(--ink-soft)', fontStyle: 'italic', fontFamily: 'var(--serif)',
    lineHeight: 'var(--lh-body)',
  },
  link: { color: 'var(--active)', textDecoration: 'underline', textDecorationStyle: 'dotted', textUnderlineOffset: '3px' },
}
