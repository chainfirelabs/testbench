import { api } from './api/client'
import { remoteTableQuery } from './remoteTable'

export function remoteTableApi<T = any>(path: string, params: URLSearchParams): Promise<T> {
  const query = remoteTableQuery(path, params)
  return api<T>(query.path, query.options)
}
