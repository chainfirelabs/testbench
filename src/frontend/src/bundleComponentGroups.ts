export interface BundleComponent {
  id?: string
  name: string
  version?: string | null
  [key: string]: unknown
}

export interface BundleComponentGroup<T extends BundleComponent> {
  name: string
  versions: T[]
}

const versionCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Group a suite's component versions by name for display, retaining every record. */
export function groupBundleComponents<T extends BundleComponent>(components: T[]): BundleComponentGroup<T>[] {
  const groups = new Map<string, BundleComponentGroup<T>>()
  for (const component of components) {
    const name = String(component.name || '').trim()
    const key = name.toLocaleLowerCase()
    let group = groups.get(key)
    if (!group) {
      group = { name, versions: [] }
      groups.set(key, group)
    }
    group.versions.push(component)
  }
  return [...groups.values()].map((group) => ({
    ...group,
    versions: group.versions.sort((a, b) => {
      const left = String(a.version || '').trim()
      const right = String(b.version || '').trim()
      if (!left) return right ? 1 : 0
      if (!right) return -1
      return versionCollator.compare(right, left)
    }),
  }))
}
