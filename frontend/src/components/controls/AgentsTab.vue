<script setup lang="ts">
import { onMounted } from 'vue'
import { useAgentsStore } from '@/stores/agents'

const agents = useAgentsStore()
let promptTimer: ReturnType<typeof setTimeout> | null = null
let fieldTimer: ReturnType<typeof setTimeout> | null = null

onMounted(() => void agents.fetchList())

function onPromptInput(name: string, text: string) {
  if (promptTimer) clearTimeout(promptTimer)
  promptTimer = setTimeout(() => {
    void agents.updatePrompt(name, text)
  }, 1000)
}

function onFieldChange(field: 'provider' | 'model' | 'temperature', value: string | number) {
  if (!agents.current) return
  if (fieldTimer) clearTimeout(fieldTimer)
  fieldTimer = setTimeout(() => {
    void agents.updateFields(agents.current!.name, { [field]: value })
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
          <dt>Provider</dt>
          <dd>
            <select
              :value="agents.current.provider"
              @change="onFieldChange('provider', ($event.target as HTMLSelectElement).value)"
            >
              <option v-for="p in agents.providers" :key="p.name" :value="p.name">{{ p.name }}</option>
            </select>
          </dd>
          <dt>Model</dt>
          <dd>
            <input
              type="text"
              :value="agents.current.model"
              @change="onFieldChange('model', ($event.target as HTMLInputElement).value)"
            />
          </dd>
          <dt>Temperature</dt>
          <dd>
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              :value="agents.current.temperature"
              @change="onFieldChange('temperature', Number(($event.target as HTMLInputElement).value))"
            />
          </dd>
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
