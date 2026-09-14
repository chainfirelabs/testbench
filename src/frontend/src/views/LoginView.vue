<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, setToken } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const ssoEnabled = ref(false)

// Handle the OIDC callback redirect: /login#token=<jwt>
function handleHashToken() {
  const hash = window.location.hash
  const match = hash.match(/token=([^&]+)/)
  if (!match) return false
  setToken(decodeURIComponent(match[1]))
  // Clean the hash so the token isn't left in the URL
  history.replaceState(null, '', window.location.pathname + window.location.search)
  return true
}

onMounted(async () => {
  if (handleHashToken()) {
    try {
      await auth.loadMe()
      if (auth.user) {
        router.push('/')
        return
      }
    } catch {
      /* fall through to the login form */
    }
  }
  try {
    const cfg = await api<{ enabled: boolean }>('/auth/oidc/config')
    ssoEnabled.value = cfg.enabled
  } catch {
    ssoEnabled.value = false
  }
})

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await auth.login(username.value, password.value)
    router.push('/')
  } catch (e: any) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function ssoLogin() {
  window.location.href = '/api/v1/auth/oidc/redirect'
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <img src="/logo-banner.png" alt="TestBench" class="login-logo" />
      <h1>Welcome to TestBench</h1>
      <p class="login-attribution">
        <span class="login-attribution-prefix">Made by </span><a
          class="login-attribution-brand"
          href="https://chainfirelabs.com"
          target="_blank"
          rel="noopener noreferrer"
        >ChainFire Labs</a>
      </p>
      <form @submit.prevent="submit">
        <input v-model="username" placeholder="Username" autocomplete="username" required />
        <input v-model="password" type="password" placeholder="Password" autocomplete="current-password" required />
        <p v-if="error" class="login-error">{{ error }}</p>
        <button class="btn btn-primary" type="submit" :disabled="busy">
          {{ busy ? 'Signing in…' : 'Log in' }}
        </button>
      </form>
      <template v-if="ssoEnabled">
        <div class="login-divider">or</div>
        <button class="btn" type="button" style="width: 100%; height: 38px" @click="ssoLogin">
          Sign in with SSO
        </button>
      </template>
    </div>
  </div>
</template>
