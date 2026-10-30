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
    key: 'linear',
    label: 'Linear 紫黑',
    preview: ['#101016', '#5e6ad2', '#1c1d24'],
  },
  {
    key: 'macos',
    label: 'macOS 毛玻璃',
    preview: ['#0a1628', '#1c75c9', '#354159'],
  },
]

