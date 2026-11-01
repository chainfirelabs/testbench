/** Merge the edited misc object and explicitly delete keys removed by the user. */
export function applyMiscData(
  data: Record<string, any>,
  value: unknown,
  previous: Record<string, any> = {},
): void {
  const edited = typeof value === 'string' ? JSON.parse(value || '{}') : value
  if (typeof edited !== 'object' || edited === null || Array.isArray(edited)) {
    throw new Error('Misc data must be a JSON object')
  }
  for (const key of Object.keys(previous)) {
    // A field now exposed by the layout has its own form value to preserve.
    if (!Object.prototype.hasOwnProperty.call(edited, key)
      && !Object.prototype.hasOwnProperty.call(data, key)) data[key] = null
  }
  Object.assign(data, edited)
}
