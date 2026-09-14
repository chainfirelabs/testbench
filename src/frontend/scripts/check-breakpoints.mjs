/**
 * Checks that the width breakpoints in the stylesheets match src/breakpoints.ts.
 *
 * The two cannot share a definition without a build step, so the constants are
 * mirrored by hand and every media query is commented with the constant it
 * mirrors. That works right up until someone changes one and not the others —
 * at which point the shell switches at one width and the cards at another, and
 * the seam only shows up on a device nobody happens to be holding.
 *
 * Run with `npm run check:breakpoints`. Exits non-zero on a mismatch.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = new URL('../src/', import.meta.url).pathname
const ROOT = new URL('../', import.meta.url).pathname

/** Pull `export const NAME = 123` out of breakpoints.ts. */
function readBreakpoints() {
  const text = readFileSync(join(SRC, 'breakpoints.ts'), 'utf8')
  const found = {}
  for (const m of text.matchAll(/export const (\w+) = (\d+)/g)) {
    found[m[1]] = Number(m[2])
  }
  return found
}

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...walk(full))
    else if (/\.(css|vue)$/.test(full)) out.push(full)
  }
  return out
}

const breakpoints = readBreakpoints()
const names = Object.keys(breakpoints)
if (!names.length) {
  console.error('check-breakpoints: no constants found in src/breakpoints.ts')
  process.exit(1)
}

/*
 * A media query is written as one pixel below the constant, because the
 * constant is where the *desktop* behaviour starts: MOBILE = 900 means the
 * mobile layout applies at 899 and below.
 */
const allowed = new Map(names.map((n) => [breakpoints[n] - 1, n]))

/*
 * Only the width in an `@media` condition counts. A bare `max-width` property
 * is an element being sized — a 620px modal, a 70ch column — and has nothing to
 * do with breakpoints.
 */
const MEDIA_WIDTH = /@media[^{]*?max-width:\s*(\d+)px/g

const problems = []
for (const file of walk(SRC)) {
  const text = readFileSync(file, 'utf8')
  for (const m of text.matchAll(MEDIA_WIDTH)) {
    const px = Number(m[1])
    if (allowed.has(px)) continue
    problems.push({
      file: relative(ROOT, file),
      line: text.slice(0, m.index).split('\n').length,
      px,
      text: m[0].replace(/\s+/g, ' ').trim(),
    })
  }
}

const summary = names
  .map((n) => `${n}=${breakpoints[n]} (media: ${breakpoints[n] - 1}px)`)
  .join(', ')

if (problems.length) {
  console.error(`check-breakpoints: FAIL\n  expected one of: ${summary}\n`)
  for (const p of problems) {
    console.error(`  ${p.file}:${p.line}  max-width: ${p.px}px`)
    console.error(`    ${p.text}`)
  }
  console.error(
    '\n  Either use a constant from src/breakpoints.ts, or add the new one there\n' +
      '  and update this list.',
  )
  process.exit(1)
}

console.log(`check-breakpoints: OK — ${summary}`)
