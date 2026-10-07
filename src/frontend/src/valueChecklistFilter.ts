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
 * Options refresh when the menu opens so another column's filter cannot leave
 * stale values in this one. Selected values stay visible until cleared.
 */
export class ValueChecklistFilter {
  private params: any
  private gui!: HTMLDivElement
  private list!: HTMLDivElement
  private search!: HTMLInputElement
  private status!: HTMLDivElement
  // Selection is independent of the values loaded for the dropdown.
  private mode: 'included' | 'excluded' = 'excluded'
  private selection = new Map<string, any>()
  private values = new Map<string, { value: any; label: string }>()
  /** Distinct values fetched from the server for the current menu opening. */
  private providerState: 'idle' | 'loading' | 'done' | 'failed' = 'idle'
  private providerRequest = 0

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
  afterGuiAttached() {
    // Other columns may have changed since this menu was last open. Discard
    // their old option domain and ignore any response still in flight.
    this.providerRequest++
    this.providerState = 'idle'
    this.search.value = ''
    this.values.clear()
    this.absorb(this.selection.values())
    this.refreshValues()
    this.search.focus()
  }
  isFilterActive() { return this.mode === 'included' || this.selection.size > 0 }
  doesFilterPass(p: any) { return this.isSelected(this.key(this.params.getValue(p.node))) }

  getModel() {
    if (!this.isFilterActive()) return null
    return { filterType: 'valueChecklist', [this.mode]: [...this.selection.values()] }
  }

  setModel(model: any) {
    this.mode = Array.isArray(model?.included) ? 'included' : 'excluded'
    this.selection.clear()
    for (const value of model?.[this.mode] || []) {
      this.selection.set(this.key(value), value == null || value === '' ? null : value)
    }
    // Saved values must remain available even when no matching rows are loaded.
    this.absorb(this.selection.values())
    if (this.list) this.renderList()
  }

  private isSelected(key: string) {
    return this.mode === 'included' ? this.selection.has(key) : !this.selection.has(key)
  }

  private select(key: string, value: any, selected: boolean) {
    if (selected === (this.mode === 'included')) this.selection.set(key, value == null || value === '' ? null : value)
    else this.selection.delete(key)
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
      try {
        const formatted = colDef.valueFormatter({
          value, data: null, node: null, column: this.params.column,
          colDef, api: this.params.api, context: this.params.context,
        })
        if (formatted != null && formatted !== '') return String(formatted)
      } catch {
        // A row-dependent formatter cannot label a distinct value without a
        // row. Keep the raw value usable instead of breaking the entire menu.
      }
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
    const request = ++this.providerRequest
    this.renderStatus()
    Promise.resolve().then(() => provide(colId, this.params.colDef))
      .then((values: any[]) => {
        if (request !== this.providerRequest) return
        this.providerState = 'done'
        this.absorb(Array.isArray(values) ? values : [])
        this.renderList()
      })
      .catch(() => {
        if (request !== this.providerRequest) return
        // A column the page cannot enumerate still filters on whatever the
        // rows on screen turned up; the panel says so rather than looking
        // like the column is empty.
        this.providerState = 'failed'
        this.absorb(this.harvestRowModel())
        this.renderList()
      })
  }

  private refreshValues() {
    if (typeof this.params.colDef?.filterParams?.valuesProvider !== 'function') {
      this.absorb(this.harvestRowModel())
    }
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
    if (!this.search.value.trim()) {
      // A global Clear means "only what I tick next"; Select all removes the
      // filter, including for values that have not arrived yet.
      this.mode = selected ? 'excluded' : 'included'
      this.selection.clear()
    } else {
      for (const [key, item] of this.matching()) this.select(key, item.value, selected)
    }
    this.renderList()
    this.params.filterChangedCallback()
  }

  private renderStatus() {
    if (!this.status) return
    if (this.providerState === 'loading') {
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
      checkbox.checked = this.isSelected(key)
      checkbox.addEventListener('change', () => {
        this.select(key, item.value, checkbox.checked)
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
