// ── Productos ──────────────────────────────────────────────

export interface CurrentPrice {
  supermarket_slug: string
  supermarket_name: string
  price: number
  currency: string
  last_updated: string // 'YYYY-MM-DD'
  url: string | null
  image_url: string | null
}

export interface ProductSummary {
  id: number
  name: string
  category: string | null
  brand: string | null
  unit: string | null
  current_prices: CurrentPrice[]
  min_price: number | null
}

export interface SupermarketProductDetail {
  id: number
  supermarket_slug: string
  supermarket_name: string
  name_raw: string
  url: string | null
  image_url: string | null
  current_price: number | null
  currency: string | null
  last_updated: string | null
}

export interface ProductDetail {
  id: number
  name: string
  category: string | null
  brand: string | null
  unit: string | null
  created_at: string
  updated_at: string
  supermarket_products: SupermarketProductDetail[]
}

export interface PricePoint {
  date: string // 'YYYY-MM-DD'
  price: number
}

export interface PriceHistory {
  product_id: number
  product_name: string
  series: Record<string, PricePoint[]> // clave: slug del supermercado
}

export interface CompareEntry {
  supermarket_slug: string
  supermarket_name: string
  price: number
  currency: string
  last_updated: string
  url: string | null
}

export interface CompareResponse {
  product: ProductSummary
  comparison: CompareEntry[] // ordenado por price ASC
  cheapest: string | null // slug del supermercado más barato
  difference: number | null // diferencia absoluta max-min
  difference_pct: number | null // ((max-min)/min) * 100
}

// ── Supermercados ──────────────────────────────────────────

export interface Supermarket {
  id: number
  slug: string
  name: string
  base_url: string
}

// ── Sistema ────────────────────────────────────────────────

export interface LastScrape {
  started_at: string
  finished_at: string | null
  status: string
  total_products_scraped: number | null
}

export interface HealthResponse {
  status: string
  database: string
  last_scrape: LastScrape | null
}

// ── Paginación ─────────────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

// ── Cliente ────────────────────────────────────────────────

const BASE_URL = ''

async function fetchJSON<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${response.statusText} — ${url}`)
  }
  return response.json() as Promise<T>
}

function buildURL(path: string, params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      search.set(key, String(value))
    }
  }
  const qs = search.toString()
  return `${BASE_URL}${path}${qs ? `?${qs}` : ''}`
}

export async function getProducts(params: {
  q?: string
  category?: string
  supermarket?: string
  page?: number
  page_size?: number
}): Promise<PaginatedResponse<ProductSummary>> {
  return fetchJSON(buildURL('/api/v1/products', params))
}

export async function getProduct(id: number): Promise<ProductDetail> {
  return fetchJSON(`${BASE_URL}/api/v1/products/${id}`)
}

export async function getProductCompare(id: number): Promise<CompareResponse> {
  return fetchJSON(`${BASE_URL}/api/v1/products/${id}/compare`)
}

export async function getProductHistory(
  id: number,
  params?: { from?: string; to?: string },
): Promise<PriceHistory> {
  return fetchJSON(buildURL(`/api/v1/products/${id}/history`, params ?? {}))
}

export async function getSupermarkets(): Promise<Supermarket[]> {
  return fetchJSON(`${BASE_URL}/api/v1/supermarkets`)
}

export async function getHealth(): Promise<HealthResponse> {
  return fetchJSON(`${BASE_URL}/health`)
}
