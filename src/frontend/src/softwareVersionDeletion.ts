export function softwareVersionDeletionUrl(
  name: string, version: string, remainingVersions: number,
): string {
  if (remainingVersions) return `/software/${encodeURIComponent(name)}`
  const label = `${name} ${version || '(unversioned)'}`
  return `/software?deleted_version=${encodeURIComponent(label)}`
}
