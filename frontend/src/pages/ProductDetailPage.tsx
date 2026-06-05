import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { getProduct, getProductCompare, type CompareResponse, type ProductDetail } from '../api/client'
import ErrorMessage from '../components/ErrorMessage'
import LoadingSpinner from '../components/LoadingSpinner'
import PriceCompareTable from '../components/PriceCompareTable'
import { formatDate } from '../utils/formatters'

export default function ProductDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [compare, setCompare] = useState<CompareResponse | null>(null)
  const [detail, setDetail] = useState<ProductDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const numericId = Number(id)

  const load = () => {
    if (!numericId) return
    setLoading(true)
    setError(null)
    setNotFound(false)

    Promise.all([getProductCompare(numericId), getProduct(numericId)])
      .then(([cmp, det]) => {
        setCompare(cmp)
        setDetail(det)
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Error desconocido'
        if (msg.includes('404')) {
          setNotFound(true)
        } else {
          setError(msg)
        }
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [numericId]) // eslint-disable-line react-hooks/exhaustive-deps

  const backButton = (
    <button
      onClick={() => navigate('/')}
      className="text-sm text-blue-600 hover:text-blue-800 hover:underline"
    >
      ← Volver al buscador
    </button>
  )

  if (loading) {
    return (
      <div className="flex justify-center py-24">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  if (notFound) {
    return (
      <div className="flex flex-col items-center gap-4 py-16 text-center">
        <h1 className="text-xl font-bold text-gray-800">Producto no encontrado</h1>
        {backButton}
      </div>
    )
  }

  if (error) {
    return <ErrorMessage message={error} retry={load} />
  }

  if (!compare || !detail) return null

  const product = compare.product
  const lastUpdated = compare.comparison[0]?.last_updated

  return (
    <div className="flex flex-col gap-6">
      {/* Navegación superior */}
      <div className="flex items-center justify-between">
        <nav className="text-sm text-gray-500 flex items-center gap-1">
          <Link to="/" className="text-blue-600 hover:underline">
            uy-precios
          </Link>
          <span>›</span>
          <span className="text-gray-800 truncate max-w-xs">{product.name}</span>
        </nav>
        {backButton}
      </div>

      {/* Cabecera del producto */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 mb-2">{product.name}</h1>
        <div className="flex flex-wrap gap-2">
          {product.category && (
            <span className="rounded-full bg-blue-100 text-blue-700 px-3 py-0.5 text-sm">
              {product.category}
            </span>
          )}
          {product.unit && (
            <span className="rounded-full bg-gray-100 text-gray-600 px-3 py-0.5 text-sm">
              {product.unit}
            </span>
          )}
          {product.brand && (
            <span className="rounded-full bg-gray-100 text-gray-600 px-3 py-0.5 text-sm">
              {product.brand}
            </span>
          )}
        </div>
      </div>

      {/* Comparación de precios */}
      <section>
        <h2 className="text-lg font-semibold text-gray-800 mb-3">Comparación de precios</h2>
        <PriceCompareTable compare={compare} />
        {lastUpdated && (
          <p className="mt-2 text-xs text-gray-400">
            Última actualización: {formatDate(lastUpdated)}
          </p>
        )}
      </section>

      {/* Nombre en cada supermercado */}
      <section>
        <h2 className="text-lg font-semibold text-gray-800 mb-3">
          Cómo aparece en cada supermercado
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                <th className="py-2 pr-6">Supermercado</th>
                <th className="py-2">Nombre en el sitio</th>
              </tr>
            </thead>
            <tbody>
              {detail.supermarket_products.map((sp) => (
                <tr key={sp.id} className="border-b border-gray-100 last:border-0">
                  <td className="py-2 pr-6 font-medium text-gray-700">{sp.supermarket_name}</td>
                  <td className="py-2 italic text-gray-600">{sp.name_raw}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Botón inferior */}
      <div>{backButton}</div>
    </div>
  )
}
