const SUPERMARKET_COLORS: Record<string, string> = {
  tienda_inglesa: '#2563eb',
  disco: '#dc2626',
}

const FALLBACK_COLORS = ['#16a34a', '#9333ea', '#ca8a04', '#0891b2']

export function getSupermarketColor(slug: string, index: number): string {
  return SUPERMARKET_COLORS[slug] ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length]
}

export function formatPrice(price: number): string {
  const formatted = new Intl.NumberFormat('es-UY', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(price)
  return `$${formatted}`
}

export function formatDate(dateStr: string): string {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString('es-UY', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

export function formatDateShort(dateStr: string): string {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString('es-UY', {
    day: 'numeric',
    month: 'short',
  })
}
