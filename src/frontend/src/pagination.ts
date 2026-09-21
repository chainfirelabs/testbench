export interface PageResult<T> {
  items: T[]
  total: number
}

/*
 * How many page requests are allowed to be in flight at once.
 *
 * Enough that a large collection is not a queue of round trips, few enough
 * that asking for one is not a burst the server has to absorb — every one of
 * these is a full query plus a count on the same connection pool.
 */
const MAX_CONCURRENT_PAGES = 4

/**
 * Fetch a complete page-number based API collection without silently truncating it.
 *
 * The first page is fetched alone, because until it comes back there is no
 * `total` and so no way to know how many more there are. Once it has, the rest
 * are requested a few at a time rather than one after another: at a thousand
 * rows a page a large fleet was a dozen sequential round trips, each one
 * waiting out a full latency before the next was even sent.
 *
 * Pages are reassembled in order regardless of the order they arrive in, so
 * the result is the same list the sequential version produced.
 */
export async function loadAllPages<T>(
  fetchPage: (page: number) => Promise<PageResult<T>>,
): Promise<PageResult<T>> {
  const first = await fetchPage(1)
  const total = first.total
  if (first.items.length >= total) return { items: first.items, total }
  if (!first.items.length) {
    // A total that promises rows the first page did not deliver: the list
    // changed underneath us, and paging on would loop without ever finishing.
    throw new Error('The list changed while loading. Please reload it.')
  }

  // Derived from what the first page actually returned rather than from any
  // page size passed in, so this stays right when the server caps the size.
  const pageSize = first.items.length
  const lastPage = Math.ceil(total / pageSize)
  const pages: T[][] = [first.items]

  for (let start = 2; start <= lastPage; start += MAX_CONCURRENT_PAGES) {
    const batch = []
    for (let page = start; page < start + MAX_CONCURRENT_PAGES && page <= lastPage; page++) {
      batch.push(fetchPage(page))
    }
    // Ordered by construction: `batch` is in page order and so is the result.
    for (const page of await Promise.all(batch)) {
      pages.push(page.items)
    }
  }

  const items = pages.flat()
  if (items.length < total) {
    // Short of what the first page promised: rows were removed while this was
    // running, so the pages after them shifted and some were never returned.
    // Reported rather than handed back, which is this function's whole reason
    // for existing — a caller cannot tell a short list from a complete one.
    throw new Error('The list changed while loading. Please reload it.')
  }
  // Longer is fine: rows added while this ran arrive on the end, and a caller
  // asking for the collection is not harmed by getting the newest of it.
  return { items, total: Math.max(total, items.length) }
}
