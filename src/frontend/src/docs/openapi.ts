/**
 * Reading the API's own OpenAPI document for the Docs page's reference: its
 * operations grouped by tag, schemas named and described, and example bodies
 * for "Try it". The document is the source of truth, so the reference is
 * always the running server's.
 */

export const METHODS = ['get', 'post', 'put', 'patch', 'delete'] as const

export interface Parameter {
  name: string
  in: 'path' | 'query' | 'header' | 'cookie'
  required?: boolean
  description?: string
  schema?: any
}

export interface Operation {
  id: string
  method: string
  path: string
  tag: string
  summary: string
  description: string
  parameters: Parameter[]
  requestBody?: { contentType: string; schema: any; required: boolean }
  responses: { status: string; description: string; schema?: any; contentType?: string }[]
  deprecated: boolean
}

export interface TagGroup { slug: string; tag: string; title: string; operations: Operation[] }

/* The order a reader meets the API in: the fleet, then evidence, then admin. */
const TAG_ORDER = [
  'auth', 'devices', 'device types', 'device schema', 'schema', 'software', 'vendor-devices',
  'tests', 'test-runs', 'test-output', 'test-artifacts', 'search', 'suggestions', 'saved_filters',
  'dashboard', 'notifications', 'users', 'roles', 'audit_logs', 'plugins', 'plugin-host',
  'ai-providers', 'replication',
]

const TAG_TITLES: Record<string, string> = {
  auth: 'Authentication & API keys', 'vendor-devices': 'Vendor claims', 'test-runs': 'Test runs',
  'test-output': 'Test output', 'test-artifacts': 'Test artifacts', saved_filters: 'Saved views',
  audit_logs: 'Audit log', 'plugin-host': 'Plugin host', 'ai-providers': 'AI providers',
  schema: 'Entity fields', default: 'Other',
}

export function tagTitle(tag: string): string {
  if (TAG_TITLES[tag]) return TAG_TITLES[tag]
  const words = tag.replace(/[-_]+/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function tagSlug(tag: string): string {
  return tag.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'other'
}

export function operationId(method: string, path: string): string {
  return `${method}-${path}`.toLowerCase().replace(/[{}]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

/** Follow a `$ref` into the document's components. */
export function deref(spec: any, schema: any, seen = 0): any {
  if (schema && schema.$ref && seen < 20) {
    const name = String(schema.$ref).split('/').pop() as string
    return deref(spec, spec?.components?.schemas?.[name], seen + 1)
  }
  return schema
}

export function refName(schema: any): string | null {
  return schema?.$ref ? String(schema.$ref).split('/').pop() as string : null
}

/** A compact type label: `string | null`, `array of Device`, `DeviceOut`. */
export function typeLabel(schema: any): string {
  if (!schema) return 'any'
  const named = refName(schema)
  if (named) return named
  const union = schema.anyOf || schema.oneOf
  if (union) return union.map(typeLabel).join(' | ')
  if (schema.allOf?.length === 1) return typeLabel(schema.allOf[0])
  if (schema.enum) return schema.enum.map((value: unknown) => JSON.stringify(value)).join(' | ')
  if (schema.const !== undefined) return JSON.stringify(schema.const)
  if (schema.type === 'array') return `array of ${typeLabel(schema.items)}`
  if (schema.type === 'object' && schema.additionalProperties && typeof schema.additionalProperties === 'object') {
    return `object of ${typeLabel(schema.additionalProperties)}`
  }
  if (schema.type === 'string' && schema.format) return `string (${schema.format})`
  return schema.type || (schema.properties ? 'object' : 'any')
}

/** The component schemas a type label names, so each can link to its definition. */
export function referencedNames(schema: any, out = new Set<string>()): Set<string> {
  if (!schema || typeof schema !== 'object') return out
  const named = refName(schema)
  if (named) {
    out.add(named)
    return out
  }
  for (const part of [...(schema.anyOf || []), ...(schema.oneOf || []), ...(schema.allOf || [])]) referencedNames(part, out)
  if (schema.items) referencedNames(schema.items, out)
  if (schema.additionalProperties && typeof schema.additionalProperties === 'object') referencedNames(schema.additionalProperties, out)
  return out
}

export interface Property { name: string; type: string; required: boolean; description: string; refs: string[]; default?: string; schema: any }

export function properties(spec: any, schema: any): Property[] {
  const resolved = deref(spec, schema)
  if (!resolved?.properties) return []
  const required = new Set<string>(resolved.required || [])
  return Object.entries<any>(resolved.properties).map(([name, value]) => ({
    name,
    type: typeLabel(value),
    required: required.has(name),
    description: value.description || deref(spec, value)?.description || '',
    refs: [...referencedNames(value)],
    default: value.default !== undefined ? JSON.stringify(value.default) : undefined,
    schema: value,
  }))
}

/** An example value for a schema, for a "Try it" body. */
export function example(spec: any, schema: any, depth = 0): any {
  const resolved = deref(spec, schema)
  if (!resolved || depth > 4) return null
  if (resolved.example !== undefined) return resolved.example
  if (resolved.default !== undefined) return resolved.default
  const union = resolved.anyOf || resolved.oneOf
  if (union) return example(spec, union.find((part: any) => part.type !== 'null') || union[0], depth)
  if (resolved.allOf?.length) return example(spec, resolved.allOf[0], depth)
  if (resolved.enum) return resolved.enum[0]
  switch (resolved.type) {
    case 'string': return resolved.format === 'date-time' ? new Date().toISOString() : resolved.format === 'date' ? new Date().toISOString().slice(0, 10) : ''
    case 'integer': case 'number': return 0
    case 'boolean': return false
    case 'array': return []
    default: break
  }
  if (resolved.properties) {
    const required = new Set<string>(resolved.required || [])
    const value: Record<string, any> = {}
    for (const [name, property] of Object.entries<any>(resolved.properties)) {
      // Required fields, plus the optional ones whose value says something.
      if (required.has(name) || depth === 0) value[name] = example(spec, property, depth + 1)
    }
    return value
  }
  return {}
}

function firstContent(content: any): { contentType: string; schema: any } | null {
  if (!content) return null
  const contentType = Object.keys(content)[0]
  return contentType ? { contentType, schema: content[contentType]?.schema } : null
}

/** Every operation in the document, grouped by tag in reading order. */
export function groupOperations(spec: any): TagGroup[] {
  const groups = new Map<string, Operation[]>()
  for (const [path, item] of Object.entries<any>(spec?.paths || {})) {
    for (const method of METHODS) {
      const op = item?.[method]
      if (!op) continue
      const tag = op.tags?.[0] || 'default'
      const body = firstContent(op.requestBody?.content)
      const operation: Operation = {
        id: operationId(method, path),
        method,
        path,
        tag,
        summary: op.summary || '',
        description: op.description || '',
        parameters: [...(item.parameters || []), ...(op.parameters || [])],
        requestBody: body ? { ...body, required: Boolean(op.requestBody?.required) } : undefined,
        responses: Object.entries<any>(op.responses || {}).map(([status, response]) => {
          const content = firstContent(response?.content)
          return { status, description: response?.description || '', schema: content?.schema, contentType: content?.contentType }
        }),
        deprecated: Boolean(op.deprecated),
      }
      if (!groups.has(tag)) groups.set(tag, [])
      groups.get(tag)!.push(operation)
    }
  }
  const rank = (tag: string) => {
    const index = TAG_ORDER.indexOf(tag)
    return index === -1 ? TAG_ORDER.length : index
  }
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([tag, operations]) => ({ slug: tagSlug(tag), tag, title: tagTitle(tag), operations }))
}

/** Fill `{name}` path parameters and build the query string for a "Try it" request. */
export function requestPath(path: string, values: Record<string, string>, parameters: Parameter[]): string {
  const filled = path.replace(/\{([^}]+)\}/g, (_, name: string) => encodeURIComponent(values[name] ?? ''))
  const query = new URLSearchParams()
  for (const parameter of parameters) {
    if (parameter.in !== 'query') continue
    const value = values[parameter.name]
    if (value !== undefined && value !== '') query.set(parameter.name, value)
  }
  const text = query.toString()
  return text ? `${filled}?${text}` : filled
}
