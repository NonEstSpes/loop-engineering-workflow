import { ref } from 'vue'

export interface ToastItem {
  id: number
  message: string
  type: 'info' | 'success' | 'warning' | 'error'
}

// Singleton state — shared across all callers.
const toasts = ref<ToastItem[]>([])
let nextId = 0

export function useToast() {
  function show(message: string, type: ToastItem['type'] = 'info') {
    const id = ++nextId
    toasts.value.push({ id, message, type })
    setTimeout(() => dismiss(id), 5000)
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  return { toasts, show, dismiss }
}
