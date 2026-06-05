import { useEffect, useMemo, useState } from 'react'
import {
  getProducts,
  getSupermarkets,
  type PaginatedResponse,
  type ProductSummary,
  type Supermarket,
} from '../api/client'
import ErrorMessage from '../components/ErrorMessage'
import LoadingSpinner from '../components/LoadingSpinner'
import ProductCard from '../components/ProductCard'
import SearchBar from '../components/SearchBar'

export default function HomePage() {
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('')
  const [supermarket, setSupermarket] = useState('')
  const [page, setPage] = useState(1)

  const [supermarkets, setSupermarkets] = useState<Supermarket[]>([])
  const [result, setResult] = useState<PaginatedResponse<ProductSummary> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Cargar supermercados una sola vez al montar
  useEffect(() => {
    getSupermarkets()
      .then(setSupermarkets)
      .catch(() => {})
  }, [])

  // Cargar productos cuando cambian los filtros
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    getProducts({
      q: q || undefined,
      category: category || undefined,
      supermarket: supermarket || undefined,
      page,
      page_size: 20,
    })
      .then((data) => {
        if (!cancelled) setResult(data)
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Error al cargar productos')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [q, category, supermarket, page])

  // Cuando cambian los filtros (no la página) resetear a página 1
  useEffect(() => {
    setPage(1)
  }, [q, category, supermarket])

  // Categorías únicas extraídas de los resultados actuales
  const categories = useMemo(() => {
    if (!result) return []
    const seen = new Set<string>()
    for (const p of result.items) {
      if (p.category) seen.add(p.category)
    }
    return Array.from(seen).sort()
  }, [result])

  // Si la categoría seleccionada ya no aparece en los resultados, resetearla
  useEffect(() => {
    if (category && categories.length > 0 && !categories.includes(category)) {
      setCategory('')
    }
  }, [categories, category])

  const retry = () => {
    setError(null)
    setPage((p) => p) // fuerza re-run del effect (mismo valor pero el setter reconecta)
    setQ((v) => v)
  }

  return (
    <div className="flex flex-col gap-6 sm:flex-row sm:gap-8">
      {/* Sidebar de filtros */}
      <aside className="w-full sm:w-64 sm:flex-shrink-0">
        <h2 className="font-bold text-gray-800 mb-3">Filtros</h2>
        <div className="flex flex-col gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1" htmlFor="filter-category">
              Categoría
            </label>
            <select
              id="filter-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-md border border-gray-300 bg-white py-1.5 px-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Todas las categorías</option>
              {categories.map((cat) => (
                <option key={cat} value={cat}>
                  {cat}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1" htmlFor="filter-supermarket">
              Supermercado
            </label>
            <select
              id="filter-supermarket"
              value={supermarket}
              onChange={(e) => setSupermarket(e.target.value)}
              className="w-full rounded-md border border-gray-300 bg-white py-1.5 px-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Todos los supermercados</option>
              {supermarkets.map((sm) => (
                <option key={sm.slug} value={sm.slug}>
                  {sm.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </aside>

      {/* Contenido principal */}
      <div className="flex-1 min-w-0">
        <SearchBar
          value={q}
          onChange={(val) => setQ(val)}
          placeholder="Buscar productos..."
        />

        {!loading && result && (
          <p className="mt-3 text-sm text-gray-500">{result.total} productos encontrados</p>
        )}

        <div className="mt-4">
          {loading ? (
            <div className="flex justify-center py-16">
              <LoadingSpinner size="lg" />
            </div>
          ) : error ? (
            <ErrorMessage message={error} retry={retry} />
          ) : result && result.items.length === 0 ? (
            <p className="text-gray-500 text-sm py-8 text-center">
              {q
                ? `No se encontraron productos para "${q}"`
                : 'No hay productos disponibles. Ejecuta el scraper primero.'}
            </p>
          ) : result ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {result.items.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </div>
          ) : null}
        </div>

        {/* Paginación */}
        {result && result.pages > 1 && (
          <div className="mt-6 flex items-center justify-center gap-4">
            <button
              onClick={() => setPage((p) => p - 1)}
              disabled={page === 1}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              ← Anterior
            </button>
            <span className="text-sm text-gray-600">
              Página {page} de {result.pages}
            </span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={page === result.pages}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Siguiente →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
