import { ref } from 'vue'

export type Theme = 'light' | 'dark'

export function useTheme() {
  const theme = ref<Theme>(
    (localStorage.getItem('devflow-theme') as Theme) || 'light'
  )

  function apply(t: Theme) {
    document.documentElement.setAttribute('data-theme', t)
    localStorage.setItem('devflow-theme', t)
  }

  function toggle() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
    apply(theme.value)
  }

  // Apply the saved theme immediately (during setup, before mount).
  apply(theme.value)

  return { theme, toggle }
}
