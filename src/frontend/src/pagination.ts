export interface PageResult<T> {
  items: T[]
  total: number
}

/** Fetch a complete page-number based API collection without silently truncating it. */
export async function loadAllPages<T>(
  fetchPage: (page: number) => Promise<PageResult<T>>,
): Promise<PageResult<T>> {
  const items: T[] = []
  let pageNumber = 1
  let total = 0
  while (true) {
    const page = await fetchPage(pageNumber)
    total = page.total
    items.push(...page.items)
    if (items.length >= total) return { items, total }
    if (!page.items.length) throw new Error('The list changed while loading. Please reload it.')
    pageNumber++
  }
}
