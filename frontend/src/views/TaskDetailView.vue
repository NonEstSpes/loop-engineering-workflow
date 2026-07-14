<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useTasksStore } from '@/stores/tasks'

const props = defineProps<{ id: string }>()
const store = useTasksStore()

async function load() {
  await store.fetchDetail(props.id)
}

onMounted(load)
watch(() => props.id, load)
</script>

<template>
  <section>
    <h2>Task: {{ props.id }}</h2>
    <p><RouterLink to="/">← Back to Dashboard</RouterLink></p>
    <p v-if="store.error" class="error">Error: {{ store.error }}</p>
    <p v-if="store.loading && !store.detail">Loading…</p>

    <div v-if="store.detail" class="detail">
      <div class="card">
        <h3>{{ store.detail.task_title }}</h3>
        <dl>
          <dt>Branch</dt><dd><code>{{ store.detail.branch_name }}</code></dd>
          <dt>Verdict</dt><dd>{{ store.detail.final_verdict ?? '–' }}</dd>
          <dt>Status</dt><dd>{{ store.detail.status }}</dd>
          <dt>Created</dt><dd>{{ store.detail.created_at }}</dd>
          <dt v-if="store.detail.published_at">Published</dt>
          <dd v-if="store.detail.published_at">{{ store.detail.published_at }}</dd>
          <dt v-if="store.detail.mr_url">MR</dt>
          <dd v-if="store.detail.mr_url">
            <a :href="store.detail.mr_url" target="_blank">{{ store.detail.mr_url }}</a>
          </dd>
        </dl>
      </div>

      <div class="card">
        <h3>Plan</h3>
        <p>{{ store.detail.plan_summary }}</p>
        <ol>
          <li v-for="step in store.detail.plan_steps" :key="step">{{ step }}</li>
        </ol>
      </div>

      <div class="card">
        <h3>Checker reports</h3>
        <ul v-if="store.detail.checker_reports.length">
          <li v-for="(r, i) in store.detail.checker_reports" :key="i">
            <strong>{{ r.agent_name }}</strong> ({{ r.verdict }}): {{ r.summary }}
            <ul v-if="r.findings.length">
              <li v-for="(f, j) in r.findings" :key="j">{{ f }}</li>
            </ul>
          </li>
        </ul>
        <p v-else>No checker reports.</p>
      </div>

      <div class="card">
        <h3>Reporter artifacts</h3>
        <p><strong>PR title:</strong> {{ store.detail.reporter_artifacts.pr_title }}</p>
        <details>
          <summary>PR description</summary>
          <pre>{{ store.detail.reporter_artifacts.pr_description }}</pre>
        </details>
        <details>
          <summary>Commit message</summary>
          <pre>{{ store.detail.reporter_artifacts.commit_message }}</pre>
        </details>
        <details>
          <summary>Corporate report</summary>
          <pre>{{ store.detail.reporter_artifacts.corporate_report }}</pre>
        </details>
      </div>

      <div class="card">
        <h3>Diff</h3>
        <details>
          <summary>Show diff</summary>
          <pre>{{ store.detail.diff }}</pre>
        </details>
      </div>
    </div>
  </section>
</template>
