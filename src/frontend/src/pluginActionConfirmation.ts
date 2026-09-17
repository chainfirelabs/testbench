interface ConfirmableAction {
  plugin_id: string
  id?: string
  scope?: 'row' | 'collection'
  label: string
  risk?: 'read_only' | 'normal' | 'disruptive'
}

/** A question required before an action starts; null means start immediately. */
export function actionConfirmation(action: ConfirmableAction, deviceLabel?: string): string | null {
  const target = deviceLabel ? ` for “${deviceLabel}”` : ''
  if (action.id === 'network-scan.scan-all' || (action.plugin_id === 'network-scan' && action.scope === 'collection')) {
    return 'Scan all devices? This scans every eligible device and may take several minutes.'
  }
  if (action.plugin_id === 'device-info-agent') {
    if (!deviceLabel) {
      return 'Gather device info for all devices? This starts an AI agent to inspect the web page of every device to find all device information.'
    }
    return `Gather device info${target}? This starts an AI agent to inspect the web page of the device to find device information.`
  }
  if (action.risk === 'disruptive') {
    const what = deviceLabel || 'every selected device'
    return `${action.label} ${what}? This interrupts whatever the device is doing.`
  }
  return null
}
