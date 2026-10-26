/** Laying out a Python signature for the Docs page's client reference. */

/** Split a parameter list on its top-level commas, leaving `dict[str, int]` whole. */
export function splitParameters(text: string): string[] {
  const parts: string[] = []
  let depth = 0
  let current = ''
  let quote = ''
  for (const char of text) {
    if (quote) {
      if (char === quote) quote = ''
    } else if (char === '"' || char === "'") quote = char
    else if ('([{'.includes(char)) depth++
    else if (')]}'.includes(char)) depth--
    else if (char === ',' && depth === 0) {
      parts.push(current.trim())
      current = ''
      continue
    }
    current += char
  }
  if (current.trim()) parts.push(current.trim())
  return parts
}

/**
 * `name(signature)` as it should read: on one line when it fits, otherwise one
 * parameter per line, the way a formatter would lay out a long call.
 */
export function formatSignature(name: string, signature: string, width = 88): string {
  if (signature.startsWith(':')) return `${name}${signature}`
  const close = closingParen(signature)
  const params = signature.slice(1, close)
  const rest = signature.slice(close + 1)
  const oneLine = `${name}(${params})${rest}`
  if (oneLine.length <= width) return oneLine
  return `${name}(\n${splitParameters(params).map((part) => `    ${part},`).join('\n')}\n)${rest}`
}

function closingParen(signature: string): number {
  let depth = 0
  for (let i = 0; i < signature.length; i++) {
    if (signature[i] === '(') depth++
    else if (signature[i] === ')' && --depth === 0) return i
  }
  return signature.length - 1
}
