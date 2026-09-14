interface ConfirmableAction {
  plugin_id: string
  label: string
  risk?: 'read_only' | 'normal' | 'disruptive'
}

/** A question required before an action starts; null means start immediately. */
export function actionConfirmation(action: ConfirmableAction, deviceLabel?: string): string | null {
  const target = deviceLabel ? ` for “${deviceLabel}”` : ''
  if (action.plugin_id === 'device-info-agent') {
    return `Gather device info${target}? This starts an AI agent to inspect the web page of the device to find device information.`
  }
  if (action.risk === 'disruptive') {
    const what = deviceLabel || 'every selected device'
    return `${action.label} ${what}? This interrupts whatever the device is doing.`
  }
  return null
}
