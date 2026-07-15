<script setup lang="ts">
import { ref } from 'vue'
import RunTab from '@/components/controls/RunTab.vue'
import TodoTab from '@/components/controls/TodoTab.vue'
import ConfigTab from '@/components/controls/ConfigTab.vue'
import AgentsTab from '@/components/controls/AgentsTab.vue'

type TabId = 'run' | 'todo' | 'config' | 'agents'
const activeTab = ref<TabId>('run')
const tabs: { id: TabId; label: string }[] = [
  { id: 'run', label: 'Run' },
  { id: 'todo', label: 'TODO' },
  { id: 'config', label: 'Config' },
  { id: 'agents', label: 'Agents' },
]
</script>

<template>
  <section>
    <h2>Controls</h2>
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.id"
        :class="{ active: activeTab === t.id }"
        @click="activeTab = t.id"
      >
        {{ t.label }}
      </button>
    </div>
    <RunTab v-if="activeTab === 'run'" />
    <TodoTab v-else-if="activeTab === 'todo'" />
    <ConfigTab v-else-if="activeTab === 'config'" />
    <AgentsTab v-else-if="activeTab === 'agents'" />
  </section>
</template>
