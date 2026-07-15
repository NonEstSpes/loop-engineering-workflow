<script setup lang="ts">
import { onMounted } from 'vue'
import { useAgentsStore } from '@/stores/agents'

const agents = useAgentsStore()
let saveTimer: ReturnType<typeof setTimeout> | null = null

onMounted(() => void agents.fetchList())

function onPromptInput(name: string, text: string) {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    void agents.updatePrompt(name, text)
  }, 1000)
}
</script>

<template>
  <section>
    <h3>Agent prompts</h3>
    <p v-if="agents.error" class="error">{{ agents.error }}</p>
    <div class="agents-layout">
      <ul class="agent-list">
        <li
          v-for="a in agents.agents"
          :key="a.name"
          :class="{ active: agents.current?.name === a.name, modified: agents.modified.has(a.name) }"
        >
          <button @click="agents.select(a.name)">
            {{ a.name }}
            <span v-if="agents.modified.has(a.name)" class="modified-dot">●</span>
          </button>
          <small>{{ a.provider }} / {{ a.model }}</small>
        </li>
      </ul>
      <div v-if="agents.current" class="agent-editor">
        <dl>
          <dt>Provider</dt><dd>{{ agents.current.provider }}</dd>
          <dt>Model</dt><dd>{{ agents.current.model }}</dd>
          <dt>Temperature</dt><dd>{{ agents.current.temperature }}</dd>
        </dl>
        <h4>System prompt</h4>
        <textarea
          :value="agents.current.system_prompt"
          @input="onPromptInput(agents.current!.name, ($event.target as HTMLTextAreaElement).value)"
          rows="16"
          class="prompt-textarea"
        ></textarea>
        <div class="actions">
          <span v-if="agents.modified.has(agents.current.name)" class="unsaved-badge">● Modified (in-memory)</span>
          <button @click="agents.save(agents.current.name)">Save to disk</button>
        </div>
      </div>
    </div>
  </section>
</template>
