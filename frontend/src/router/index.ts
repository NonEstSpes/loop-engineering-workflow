import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('@/views/DashboardView.vue') },
  { path: '/approvals', name: 'approvals', component: () => import('@/views/ApprovalsView.vue') },
  { path: '/eod', name: 'eod', component: () => import('@/views/EodReviewView.vue') },
  { path: '/tasks/:id', name: 'task-detail', component: () => import('@/views/TaskDetailView.vue'), props: true },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
