/**
 * The Python client's reference, generated from its code by
 * src/python-client/scripts/build_docs.py — never edit pythonClient.json by
 * hand; the client's test suite fails when it drifts from the code.
 */
import reference from './pythonClient.json'

export interface PyMember { name: string; kind: 'method' | 'classmethod' | 'property'; signature: string; doc: string }
export interface PyResource { slug: string; accessor: string; class: string; doc: string; members: PyMember[]; init_signature?: string }
export interface PyModel { slug: string; name: string; doc: string; fields: { name: string; type: string; default: string | null }[]; members: PyMember[] }
export interface PyError { name: string; base: string; doc: string; members: PyMember[] }
export interface PyReference {
  version: string
  guide: { slug: string; title: string; markdown: string }[]
  reference: PyResource[]
  models: PyModel[]
  errors: PyError[]
  constants: { name: string; value: string }[]
}

export const PYTHON: PyReference = reference as unknown as PyReference

export { formatSignature } from './signature'
