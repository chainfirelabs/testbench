/**
 * A value checklist for AG Grid column filters — the Community-edition
 * equivalent of the Enterprise Set Filter.
 *
 * Its own module rather than a class inside DataTable.vue: none of it is Vue,
 * all of it is worth testing on its own, and "which values does this column
 * offer?" is the question the whole thing exists to answer.
 *
 * The values it offers come from three places, in order of how much they know:
 *
 * 1. `valuesProvider` — the page's own answer to "every distinct value this
 *    column holds", fetched from the server. Required for a server-paged
 *    table: the browser only ever holds one window of those, so harvesting the
 *    row model there offers the hundred values on screen and hides the rest.
 * 2. The client-side row model (`forEachLeafNode`), which has every row.
 * 3. The rows currently loaded (`forEachNode`), which is what an infinite row
 *    model can answer on its own. A fallback, so a page with no provider wired
 *    up still offers something to tick rather than an empty panel.
 *
 * Whatever they turn up accumulates: a value seen on an earlier page stays on
 * the list after paging away from it, so the choice does not move underneath
 * the user.
 */
export class ValueChecklistFilter {
  private params: any
  private gui!: HTMLDivElement
  private list!: HTMLDivElement
  private search!: HTMLInputElement
  private status!: HTMLDivElement
  // Store excluded raw values so newly loaded values remain visible by default.
  private excluded = new Map<string, any>()
  private values = new Map<string, { value: any; label: string }>()
  /** Distinct values fetched from the server, once per filter instance. */
  private providerState: 'idle' | 'loading' | 'done' | 'failed' = 'idle'

  init(params: any) {
    this.params = params
    this.gui = document.createElement('div')
    this.gui.className = 'value-checklist-filter'
    this.search = document.createElement('input')
    this.search.type = 'search'
    this.search.placeholder = 'Find value…'
    this.search.setAttribute('aria-label', 'Find filter value')
    this.search.addEventListener('input', () => this.renderList())
    this.gui.appendChild(this.search)

    const actions = document.createElement('div')
    actions.className = 'value-checklist-actions'
    // "Select all" and "Clear" act on what the search box is showing, not on
    // the whole column: typing "Dell" and clicking Clear means "untick the
    // Dells", which is the only reading that makes the search box useful for
    // anything but finding one row to tick.
    actions.append(this.actionButton('Select all', () => this.setAll(true)))
    actions.append(this.actionButton('Clear', () => this.setAll(false)))
    this.gui.appendChild(actions)
    this.list = document.createElement('div')
    this.list.className = 'value-checklist-options'
    this.gui.appendChild(this.list)
    this.status = document.createElement('div')
    this.status.className = 'value-checklist-status'
    this.gui.appendChild(this.status)
    this.refreshValues()
  }

  getGui() { return this.gui }
  afterGuiAttached() { this.refreshValues(); this.search.focus() }
  isFilterActive() { return this.excluded.size > 0 }
  doesFilterPass(p: any) { return !this.excluded.has(this.key(this.params.getValue(p.node))) }
  /*
   * The model carries whichever side of the selection is shorter.
   *
   * It travels in the query string. Unticking three values out of five hundred
   * is three values to send; clearing the list and ticking one is four hundred
   * and ninety-nine, which is sixteen kilobytes of URL and a 414 from the
   * proxy long before it reaches the API — the grid then shows nothing at all,
   * which looks exactly like a filter that matched nothing.
   *
   * The two are not quite the same statement. `excluded` hides what it names
   * and lets anything else through, including a value this list has never
   * seen; `included` shows only what it names. That difference is real but it
   * falls where it should: someone who unticked a few means "not those", and
   * someone who cleared the list and ticked two means "just these two".
   */
  getModel() {
    if (!this.isFilterActive()) return null
    const included: any[] = []
    for (const [key, item] of this.values) {
      if (!this.excluded.has(key)) included.push(item.value)
    }
    return included.length < this.excluded.size
      ? { filterType: 'valueChecklist', included }
      : { filterType: 'valueChecklist', excluded: [...this.excluded.values()] }
  }
  setModel(model: any) {
    this.excluded.clear()
    if (Array.isArray(model?.included)) {
      // Restored from the other form: everything known that is not named is
      // excluded, which is what "show only these" means on the way back in.
      this.absorb(model.included)
      const keep = new Set(model.included.map((value: any) => this.key(value)))
      for (const [key, item] of this.values) {
        if (!keep.has(key)) this.excluded.set(key, item.value)
      }
    }
    for (const value of model?.excluded || []) this.excluded.set(this.key(value), value)
    /*
     * An excluded value is always offered, whatever else is known.
     *
     * Otherwise the filter can reach a state it cannot be talked out of. The
     * values come from the row model when a column has no provider — the
     * software columns on the claims page, anything a page lists in `skip` —
     * and a filter narrow enough to leave no rows leaves nothing to harvest.
     * Rebuild the panel from there and it offers an empty list while still
     * excluding things: every row hidden, and no checkbox to untick.
     *
     * Whatever this filter is hiding, it can say so and let it back.
     */
    this.absorb(this.excluded.values())
    if (this.list) this.renderList()
  }

  private key(value: any): string {
    if (value == null || value === '') return '__blank__'
    if (typeof value === 'object') return `object:${JSON.stringify(value)}`
    return `${typeof value}:${String(value)}`
  }

  private label(value: any): string {
    if (value == null || value === '') return '(Blanks)'
    const colDef = this.params.column?.getColDef?.()
    if (typeof colDef?.valueFormatter === 'function') {
      const formatted = colDef.valueFormatter({
        value, data: null, node: null, column: this.params.column,
        colDef, api: this.params.api, context: this.params.context,
      })
      if (formatted != null && formatted !== '') return String(formatted)
    }
    return typeof value === 'object' ? JSON.stringify(value) : String(value)
  }

  /** Fold values into the accumulated list, preserving what was already known. */
  private absorb(values: Iterable<any>) {
    for (const value of values) {
      const key = this.key(value)
      if (!this.values.has(key)) this.values.set(key, { value, label: this.label(value) })
    }
    this.values = new Map([...this.values].sort((a, b) => a[1].label.localeCompare(b[1].label)))
  }

  /** Every value in the row model the browser holds, whichever model that is. */
  private harvestRowModel(): any[] {
    const values: any[] = []
    const collect = (node: any) => {
      if (node?.data == null) return
      values.push(this.params.getValue(node))
    }
    // Client-side only; on an infinite row model this is a no-op rather than
    // an error, which is why the second pass is unconditional.
    this.params.api.forEachLeafNode?.(collect)
    if (!values.length) this.params.api.forEachNode?.(collect)
    return values
  }

  /**
   * Ask the page for the column's distinct values.
   *
   * Once per open filter, not once per keystroke: the lists are short enough
   * to filter in the browser, and a request per character on a column with no
   * index on its text is the expensive way to get a worse result.
   */
  private loadProvidedValues() {
    if (this.providerState !== 'idle') return
    const provide = this.params.colDef?.filterParams?.valuesProvider
    const colId = this.params.column?.getColId?.()
    if (typeof provide !== 'function' || !colId) {
      this.providerState = 'done'
      return
    }
    this.providerState = 'loading'
    this.renderStatus()
    Promise.resolve(provide(colId, this.params.colDef))
      .then((values: any[]) => {
        this.providerState = 'done'
        this.absorb(Array.isArray(values) ? values : [])
        this.renderList()
      })
      .catch(() => {
        // A column the page cannot enumerate still filters on whatever the
        // rows on screen turned up; the panel says so rather than looking
        // like the column is empty.
        this.providerState = 'failed'
        this.renderList()
      })
  }

  private refreshValues() {
    this.absorb(this.harvestRowModel())
    this.loadProvidedValues()
    this.renderList()
  }

  private actionButton(label: string, run: () => void): HTMLButtonElement {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = label
    button.addEventListener('click', run)
    return button
  }

  /** The entries the search box is currently showing. */
  private matching(): [string, { value: any; label: string }][] {
    const query = this.search?.value.trim().toLocaleLowerCase() || ''
    if (!query) return [...this.values]
    return [...this.values].filter(([, item]) => item.label.toLocaleLowerCase().includes(query))
  }

  private setAll(selected: boolean) {
    for (const [key, item] of this.matching()) {
      if (selected) this.excluded.delete(key)
      else this.excluded.set(key, item.value)
    }
    this.renderList()
    this.params.filterChangedCallback()
  }

  private renderStatus() {
    if (!this.status) return
    if (this.providerState === 'loading' && !this.values.size) {
      this.status.textContent = 'Loading values…'
    } else if (!this.values.size) {
      this.status.textContent = 'No values to filter on.'
    } else if (this.providerState === 'failed') {
      this.status.textContent = 'Showing values from the rows loaded so far.'
    } else {
      this.status.textContent = ''
    }
    this.status.hidden = !this.status.textContent
  }

  private renderList() {
    if (!this.list) return
    this.list.replaceChildren()
    for (const [key, item] of this.matching()) {
      const row = document.createElement('label')
      row.className = 'value-checklist-option'
      const checkbox = document.createElement('input')
      checkbox.type = 'checkbox'
      checkbox.checked = !this.excluded.has(key)
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) this.excluded.delete(key)
        else this.excluded.set(key, item.value)
        this.params.filterChangedCallback()
      })
      const text = document.createElement('span')
      text.textContent = item.label
      row.append(checkbox, text)
      this.list.appendChild(row)
    }
    this.renderStatus()
  }
}
