/**
 * How a finished device scan reads in the UI.
 *
 * The scan probes the WAN and the LAN address separately, and which one
 * answered is worth reporting: a device reachable on its LAN address but not
 * its WAN address is a different situation from one that is simply up. The API
 * returns that per address in `probes`; this turns it into the short badge the
 * grid shows and the sentence the toast shows.
 */

export interface ScanProbe {
  role: string
  ip: string
  online: boolean
  services: string[]
}

export interface ScanResult {
  online: boolean
  probes?: ScanProbe[]
  error?: string | null
}

export interface ScanDescription {
  /** Fits in a grid row badge. */
  badge: string
  /** Reads as the tail of "<device>: ..." in a toast. */
  detail: string
  /** row-badge modifier: ok | off | error */
  cls: string
}

const roleLabel = (role: string) =>
  role === 'wan' ? 'WAN' : role === 'lan' ? 'LAN' : role.toUpperCase()

export function describeScan(result: ScanResult): ScanDescription {
  if (result.error) {
    return { badge: 'Scan failed', detail: `scan failed — ${result.error}`, cls: 'error' }
  }

  const probes = result.probes || []
  // No address to probe. Reporting this as "offline" would claim a check that
  // never ran — and it is the one case the user can fix by editing the device.
  if (!probes.length) {
    return { badge: 'No IP', detail: 'no WAN or LAN IP set — nothing to scan', cls: 'off' }
  }

  const up = probes.filter((p) => p.online)
  if (!up.length) {
    const tried = probes.map((p) => `${roleLabel(p.role)} ${p.ip}`).join(' and ')
    return { badge: '✗ Offline', detail: `offline — no response on ${tried}`, cls: 'off' }
  }

  const where = up.map((p) => `${roleLabel(p.role)} ${p.ip} (${p.services.join(', ')})`).join('; ')
  return {
    badge: `✓ Online (${up.map((p) => roleLabel(p.role)).join('+')})`,
    detail: `online via ${where}`,
    cls: 'ok',
  }
}
