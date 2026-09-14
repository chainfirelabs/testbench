import { ref } from 'vue'
import { api } from './api/client'

export interface DeviceType {
  id: string
  key: string
  label: string
  description?: string | null
  enabled: boolean
  position: number
  device_count: number
  /** Which side owns this row: the admin GUI, a YAML document, or the seed. */
  configuration_source: 'system' | 'gui' | 'yaml'
  field_count: number
  required_field_keys: string[]
  plugin_ids: string[]
}

export const deviceTypes = ref<DeviceType[]>([])

export async function loadDeviceTypes(includeDisabled = false) {
  deviceTypes.value = await api<DeviceType[]>(`/device-types${includeDisabled ? '?include_disabled=true' : ''}`)
}
