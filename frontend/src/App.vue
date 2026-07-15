<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import { useSSE, sseConnectionState } from '@/composables/useSSE'
import { useToast } from '@/composables/useToast'
import { useTheme } from '@/composables/useTheme'

useSSE()
const { toasts, dismiss } = useToast()
const { theme, toggle } = useTheme()
</script>

<template>
  <div id="devflow-app">
    <header>
      <h1>DevFlow Dashboard</h1>
      <nav>
        <RouterLink to="/">Dashboard</RouterLink>
        <RouterLink to="/approvals">Approvals</RouterLink>
        <RouterLink to="/eod">EOD Review</RouterLink>
        <RouterLink to="/controls">Controls</RouterLink>
        <RouterLink to="/activity">Activity</RouterLink>
      </nav>
      <div class="header-right">
        <span
          :class="['sse-dot', sseConnectionState]"
          :title="`SSE: ${sseConnectionState}`"
        ></span>
        <button class="theme-toggle" @click="toggle" :title="theme === 'light' ? 'Dark mode' : 'Light mode'">
          {{ theme === 'light' ? '🌙' : '☀️' }}
        </button>
      </div>
    </header>
    <main>
      <RouterView />
    </main>
    <div class="toast-container">
      <div
        v-for="t in toasts"
        :key="t.id"
        :class="['toast', `toast-${t.type}`]"
        @click="dismiss(t.id)"
      >
        {{ t.message }}
      </div>
    </div>
  </div>
</template>
