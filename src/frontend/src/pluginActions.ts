import { ref } from 'vue'
import { api } from './api/client'

export interface PluginAction {
  plugin_id: string
  id: string
  entity: string
  scope: 'row' | 'collection'
  label: string
  title?: string
  icon?: 'wifi' | 'info' | 'power'
  /** read_only | normal | disruptive. Disruptive actions say so before they run. */
  risk?: 'read_only' | 'normal' | 'disruptive'
  requires_online?: boolean
  required_roles?: string[]
  /** Each group is satisfied by any one of its roles (WAN address or LAN address). */
  required_role_groups?: string[][]
  required_any_roles?: string[]
  allow_global_assignment?: boolean
  /** The device types this plugin is enabled for. Empty means nobody. */
  device_types?: string[]
  unavailable_reasons_by_type?: Record<string, string>
  unavailable_reason?: string
  /** Only on the per-device endpoint, where the backend has decided. */
  available?: boolean
}

/**
 * The actions a page may offer, scoped to one device type where there is one.
 *
 * This is what the UI renders from, not what authorises anything: the
 * invocation endpoint decides that again for every call, so a request the UI
 * would never have made is still refused.
 */
export function usePluginActions(entity: string) {
  const actions = ref<PluginAction[]>([])
  async function loadPluginActions(deviceType?: string) {
    const scope = deviceType ? `&device_type=${encodeURIComponent(deviceType)}` : ''
    try {
      actions.value = await api<PluginAction[]>(
        `/plugins/actions?entity=${encodeURIComponent(entity)}${scope}`,
      )
    } catch {
      actions.value = []
    }
  }
  return { actions, loadPluginActions }
}

/** The backend's own verdict for one device, with a reason for each refusal. */
export async function loadDeviceActions(deviceId: string): Promise<PluginAction[]> {
  try {
    const result = await api<{ actions: PluginAction[] }>(`/devices/${encodeURIComponent(deviceId)}/actions`)
    return result.actions
  } catch {
    return []
  }
}

/** Every role an action needs: the required ones plus one from each group. */
export function actionRoleGroups(action: PluginAction): string[][] {
  const groups = [...(action.required_role_groups || [])]
  if (action.required_any_roles?.length) groups.push(action.required_any_roles)
  return groups
}

export async function invokePluginAction(action: PluginAction, body: Record<string, any>) {
  return api<{ run_id?: string }>(
    `/plugins/${encodeURIComponent(action.plugin_id)}/actions/${encodeURIComponent(action.id)}/invoke`,
    { method: 'POST', body: JSON.stringify({ entity: action.entity, ...body }) },
  )
}
