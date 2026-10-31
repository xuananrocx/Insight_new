import type { ThemeKey } from '@/hooks/use-theme'

export const THEMES: { key: ThemeKey; label: string; preview: string[] }[] = [
  {
    key: 'minimal',
    label: '简约灰白',
    preview: ['#fafafa', '#35383d', '#f3f3f1'],
  },
  {
    key: 'stripe-light',
    label: 'Stripe 浅色',
    preview: ['#f6f9fc', '#635bff', '#e3e8ee'],
  },
  {
    key: 'everforest',
    label: 'Everforest 森林',
    preview: ['#2d353b', '#a7c080', '#232a2e'],
  },
  {
    key: 'macos',
    label: 'macOS 毛玻璃',
    preview: ['#0a1628', '#1c75c9', '#354159'],
  },
]

