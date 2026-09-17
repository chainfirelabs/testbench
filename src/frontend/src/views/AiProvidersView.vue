<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'

interface Profile {
  id: string; name: string; provider_type: string; base_url: string
  credential_configured: boolean; custom_headers: Record<string, string>
  manual_models: string[]; models: string[]; enabled: boolean
  last_error?: string; last_refreshed_at?: string
}
interface PluginDefault {
  plugin_id: string; locked: boolean; helm: Record<string, any>
  profile_id?: string; model?: string; repeat_model?: string
}

const profiles = ref<Profile[]>([])
const defaults = ref<PluginDefault[]>([])
const editing = ref<any | null>(null)
const busy = ref(false)
const toast = ref('')
const error = ref('')
const labels: Record<string, string> = {
  'device-info-agent': 'Device Info', 'device-reboot': 'AI Reboot',
}
const enabledProfiles = computed(() => profiles.value.filter((item) => item.enabled))
const allDefaultsLocked = computed(() => defaults.value.length > 0 && defaults.value.every((item) => item.locked))
const showGuiProviders = computed(() => profiles.value.length > 0 || !allDefaultsLocked.value)
const modelsFor = (profileId?: string) => profiles.value.find((item) => item.id === profileId)?.models || []

async function load() {
  ;[profiles.value, defaults.value] = await Promise.all([
    api<Profile[]>('/ai/providers'), api<PluginDefault[]>('/ai/plugin-defaults'),
  ])
}
function startNew() {
  editing.value = { name: '', provider_type: 'openai-compatible', base_url: '', api_key: '', custom_headers: {}, manual_models: [], enabled: true }
}
function startEdit(item: Profile) {
  editing.value = { ...item, api_key: '', clear_api_key: false, manual_models_text: item.manual_models.join('\n'), custom_headers_text: JSON.stringify(item.custom_headers || {}, null, 2) }
}
async function saveProfile() {
  busy.value = true; error.value = ''
  try {
    const item = editing.value
    let headers = item.custom_headers || {}
    if (item.custom_headers_text != null) headers = JSON.parse(item.custom_headers_text || '{}')
    const payload = {
      name: item.name, provider_type: item.provider_type, base_url: item.base_url,
      api_key: item.api_key || null, clear_api_key: !!item.clear_api_key,
      custom_headers: headers,
      manual_models: item.manual_models_text != null ? item.manual_models_text.split('\n').map((x: string) => x.trim()).filter(Boolean) : item.manual_models,
      enabled: item.enabled,
    }
    await api(item.id ? `/ai/providers/${item.id}` : '/ai/providers', {
      method: item.id ? 'PUT' : 'POST', body: JSON.stringify(payload),
    })
    editing.value = null; await load(); toast.value = 'AI provider saved'
  } catch (e: any) { error.value = e.message } finally { busy.value = false }
}
async function remove(item: Profile) {
  if (!confirm(`Delete AI provider “${item.name}”?`)) return
  await api(`/ai/providers/${item.id}`, { method: 'DELETE' }); await load()
}
async function refresh(item: Profile) {
  error.value = ''
  try { await api(`/ai/providers/${item.id}/refresh-models`, { method: 'POST' }); await load() }
  catch (e: any) { error.value = e.message; await load() }
}
async function saveDefault(item: PluginDefault) {
  await api(`/ai/plugin-defaults/${item.plugin_id}`, {
    method: 'PUT', body: JSON.stringify({ profile_id: item.profile_id || null, model: item.model || null, repeat_model: item.repeat_model || null }),
  })
  toast.value = `${labels[item.plugin_id]} defaults saved`; await load()
}
onMounted(() => load().catch((e) => { error.value = e.message }))
</script>

<template>
  <div class="page page-flow">
    <div class="page-header"><h2>AI Providers</h2><button v-if="!allDefaultsLocked" class="btn btn-primary" @click="startNew">Add provider</button></div>
    <p class="muted">Admin-managed endpoints and credentials are used only when the corresponding Helm URL and model are empty.</p>
    <p v-if="error" class="login-error">{{ error }}</p><p v-if="toast" class="toast">{{ toast }}</p>

    <section class="card" v-for="item in defaults" :key="item.plugin_id">
      <h3>{{ labels[item.plugin_id] }}</h3>
      <template v-if="item.locked">
        <p class="muted">Managed by Helm; GUI overrides are disabled.</p>
        <div class="form-grid">
          <label>Endpoint<input :value="item.helm.url" disabled /></label>
          <label>Model<input :value="item.helm.model" disabled /></label>
          <label>Repeat model<input :value="item.helm.repeat_model || item.helm.model" disabled /></label>
        </div>
      </template>
      <template v-else>
        <div class="form-grid">
          <label>Provider<select v-model="item.profile_id"><option value="">Select provider</option><option v-for="p in enabledProfiles" :key="p.id" :value="p.id">{{ p.name }}</option></select></label>
          <label>Model<input v-model="item.model" :list="`models-${item.plugin_id}`" /></label>
          <label>Repeat model<input v-model="item.repeat_model" :list="`models-${item.plugin_id}`" placeholder="Defaults to model" /></label>
          <datalist :id="`models-${item.plugin_id}`"><option v-for="model in modelsFor(item.profile_id)" :key="model" :value="model" /></datalist>
        </div>
        <button class="btn btn-primary" @click="saveDefault(item)">Save defaults</button>
      </template>
    </section>

    <section v-if="showGuiProviders" class="card"><div class="section-head"><h3>GUI-managed providers</h3></div>
      <p v-if="!profiles.length" class="muted">No GUI-managed AI providers configured.</p>
      <div v-for="item in profiles" :key="item.id" class="plugin-card">
        <strong>{{ item.name }}</strong> <span class="muted">{{ item.provider_type }} · {{ item.base_url }}</span>
        <p class="muted">{{ item.models.length }} models · credential {{ item.credential_configured ? 'configured' : 'not configured' }}</p>
        <p v-if="item.last_error" class="login-error">{{ item.last_error }}</p>
        <div><button class="btn" @click="refresh(item)">Refresh models</button> <button class="btn" @click="startEdit(item)">Edit</button> <button class="btn btn-danger" @click="remove(item)">Delete</button></div>
      </div>
    </section>

    <div v-if="editing" class="modal-backdrop"><div class="modal card"><h3>{{ editing.id ? 'Edit' : 'Add' }} AI provider</h3>
      <div class="form-grid">
        <label>Name<input v-model="editing.name" /></label>
        <label>Type<select v-model="editing.provider_type"><option value="openai">OpenAI</option><option value="openai-compatible">OpenAI-compatible</option><option value="azure-openai">Azure OpenAI</option><option value="ollama">Ollama</option><option value="litellm">LiteLLM/OpenRouter</option></select></label>
        <label>Base URL<input v-model="editing.base_url" placeholder="https://ai.example/v1" /></label>
        <label>API key<input v-model="editing.api_key" type="password" :placeholder="editing.credential_configured ? 'Leave blank to keep current key' : 'Optional'" /></label>
        <label>Manual models (one per line)<textarea v-model="editing.manual_models_text"></textarea></label>
        <label>Model-discovery headers (JSON)<textarea v-model="editing.custom_headers_text"></textarea></label>
        <label class="check"><input v-model="editing.enabled" type="checkbox" /> Enabled</label>
        <label v-if="editing.credential_configured" class="check"><input v-model="editing.clear_api_key" type="checkbox" /> Remove stored API key</label>
      </div>
      <div><button class="btn" @click="editing = null">Cancel</button> <button class="btn btn-primary" :disabled="busy" @click="saveProfile">Save</button></div>
    </div></div>
  </div>
</template>

<style scoped>
.card{padding:18px;margin-bottom:16px}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:12px 0}.form-grid label{display:flex;flex-direction:column;gap:6px}.plugin-card{padding:14px 0;border-top:1px solid var(--border)}.modal-backdrop{position:fixed;inset:0;background:#0009;display:grid;place-items:center;z-index:400}.modal{width:min(760px,92vw);max-height:90vh;overflow:auto}.check{flex-direction:row!important;align-items:center}.toast{color:var(--accent)}
</style>
