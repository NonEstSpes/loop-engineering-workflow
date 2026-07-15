<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import { useSSE } from '@/composables/useSSE'

// Connect to the live event stream for the whole app.
useSSE()
</script>

<template>
  <div id="devflow-app">
    <header>
      <h1>DevFlow Dashboard</h1>
      <nav>
        <RouterLink to="/">Dashboard</RouterLink>
        <span> · </span>
        <RouterLink to="/approvals">Approvals</RouterLink>
        <span> · </span>
        <RouterLink to="/eod">EOD Review</RouterLink>
        <span> · </span>
        <RouterLink to="/controls">Controls</RouterLink>
      </nav>
    </header>
    <main>
      <RouterView />
    </main>
  </div>
</template>
