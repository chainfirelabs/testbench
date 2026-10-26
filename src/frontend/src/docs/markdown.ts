/**
 * Markdown for the Docs page: the guide pages, the Python client's README and
 * docstrings, and the descriptions in the API's OpenAPI document.
 *
 * Small on purpose — headings, paragraphs, lists, tables, quotes, fenced code
 * and the inline basics — because that is all those sources use. Every piece
 * of text is HTML-escaped before any markup is added, so nothing in a
 * docstring or a spec can become an element; links are kept only for http(s),
 * absolute and in-page targets.
 */

export interface Heading { id: string; text: string; level: number }

export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

export function slugify(text: string): string {
  return text.toLowerCase().replace(/<[^>]*>/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

const SAFE_LINK = /^(https?:\/\/|\/|#)/i

/** Inline markup on one run of text: code spans first, so their contents stay literal. */
export function inline(text: string): string {
  return text.split(/(`[^`]+`)/g).map((part) => {
    if (part.length > 1 && part.startsWith('`') && part.endsWith('`')) {
      return `<code>${escapeHtml(part.slice(1, -1))}</code>`
    }
    let html = escapeHtml(part)
    html = html.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label: string, href: string) => {
      const target = href.replace(/&amp;/g, '&')
      if (!SAFE_LINK.test(target)) return label
      const external = /^https?:/i.test(target)
      return `<a href="${escapeHtml(target)}"${external ? ' target="_blank" rel="noopener noreferrer"' : ''}>${label}</a>`
    })
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    // Single-asterisk emphasis only: underscores are everywhere in identifiers.
    html = html.replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>')
    return html
  }).join('')
}

const PY_KEYWORDS = new Set(('False None True and as assert async await break class continue def del elif else except '
  + 'finally for from global if import in is lambda nonlocal not or pass raise return try while with yield').split(' '))
const SH_KEYWORDS = new Set('export curl pip python cd echo if then fi for do done'.split(' '))

/** A light highlight for the code blocks the docs use: comments, strings, keywords, numbers. */
export function highlight(code: string, lang: string): string {
  const language = (lang || '').toLowerCase()
  if (!['python', 'py', 'bash', 'sh', 'shell', 'json', 'http'].includes(language)) return escapeHtml(code)
  const comment = language === 'json' ? null : /#.*$/
  const keywords = language.startsWith('py') ? PY_KEYWORDS : language === 'json' ? new Set(['true', 'false', 'null']) : SH_KEYWORDS
  const token = /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|#[^\n]*|\b\d+(?:\.\d+)?\b|[A-Za-z_][A-Za-z0-9_]*)/g
  let out = ''
  let last = 0
  for (const match of code.matchAll(token)) {
    const value = match[0]
    const index = match.index ?? 0
    out += escapeHtml(code.slice(last, index))
    last = index + value.length
    let kind = ''
    if (comment && value.startsWith('#')) kind = 'tok-comment'
    else if (/^["']/.test(value)) kind = language === 'json' && /^\s*:/.test(code.slice(last)) ? 'tok-key' : 'tok-string'
    else if (/^\d/.test(value)) kind = 'tok-number'
    else if (keywords.has(value)) kind = 'tok-keyword'
    else if (language.startsWith('py') && /^\s*\(/.test(code.slice(last)) && /\bdef\s+$/.test(code.slice(0, index))) kind = 'tok-def'
    out += kind ? `<span class="${kind}">${escapeHtml(value)}</span>` : escapeHtml(value)
  }
  return out + escapeHtml(code.slice(last))
}

function codeBlock(code: string, lang: string): string {
  const label = lang ? `<span class="code-lang">${escapeHtml(lang)}</span>` : ''
  return `<div class="code-block">${label}<pre><code>${highlight(code, lang)}</code></pre></div>`
}

function table(rows: string[]): string {
  // `\|` is a pipe inside a cell, not the end of one.
  const cells = (row: string) => row.trim().replace(/^\||(?<!\\)\|$/g, '').split(/(?<!\\)\|/)
    .map((cell) => cell.trim().replace(/\\\|/g, '|'))
  const [head, , ...body] = rows
  return '<div class="table-wrap"><table><thead><tr>'
    + cells(head).map((cell) => `<th>${inline(cell)}</th>`).join('')
    + '</tr></thead><tbody>'
    + body.map((row) => `<tr>${cells(row).map((cell) => `<td>${inline(cell)}</td>`).join('')}</tr>`).join('')
    + '</tbody></table></div>'
}

/**
 * Render markdown to HTML. `headings` collects each `##`/`###` heading with
 * the id it was given, for the page's table of contents; `baseLevel` shifts
 * headings down, so a docstring's `#` never outranks the page's own title.
 */
export function renderMarkdown(source: string, headings: Heading[] = [], baseLevel = 0): string {
  const lines = (source || '').replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []
  const used = new Set(headings.map((heading) => heading.id))
  let i = 0
  let paragraph: string[] = []
  const flush = () => {
    if (paragraph.length) out.push(`<p>${inline(paragraph.join(' '))}</p>`)
    paragraph = []
  }
  while (i < lines.length) {
    const line = lines[i]
    const fence = line.match(/^(\s*)```\s*([\w+-]*)\s*$/)
    if (fence) {
      flush()
      const indent = fence[1].length
      const body: string[] = []
      i++
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++].slice(indent))
      i++
      out.push(codeBlock(body.join('\n'), fence[2]))
      continue
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      flush()
      const level = Math.min(6, heading[1].length + 1 + baseLevel)
      let id = slugify(heading[2]) || 'section'
      while (used.has(id)) id += '-'
      used.add(id)
      headings.push({ id, text: heading[2].replace(/`/g, ''), level })
      out.push(`<h${level} id="${id}">${inline(heading[2])}</h${level}>`)
      i++
      continue
    }
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || '')) {
      flush()
      const rows: string[] = []
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++])
      out.push(table(rows))
      continue
    }
    if (/^>\s?/.test(line)) {
      flush()
      const quote: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^>\s?/, ''))
      out.push(`<div class="admonition">${renderMarkdown(quote.join('\n'), headings, baseLevel)}</div>`)
      continue
    }
    const item = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/)
    if (item) {
      flush()
      const ordered = /\d/.test(item[2])
      const items: string[] = []
      while (i < lines.length) {
        const current = lines[i].match(/^(\s*)([-*]|\d+\.)\s+(.*)$/)
        if (current && current[1].length <= item[1].length) {
          items.push(current[3])
          i++
        } else if (lines[i].trim() && /^\s+/.test(lines[i]) && items.length) {
          // A continuation line, or a nested item, folded into the current one.
          items[items.length - 1] += `\n${lines[i].trim()}`
          i++
        } else break
      }
      const tag = ordered ? 'ol' : 'ul'
      out.push(`<${tag}>${items.map((text) => `<li>${inline(text.replace(/\n[-*]\s+/g, ' · ').replace(/\n/g, ' '))}</li>`).join('')}</${tag}>`)
      continue
    }
    if (!line.trim()) {
      flush()
      i++
      continue
    }
    paragraph.push(line.trim())
    i++
  }
  flush()
  return out.join('\n')
}
