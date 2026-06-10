import { useNavigate } from 'react-router-dom'
import { type CurrentPrice, type ProductSummary } from '../api/client'
import { formatPrice } from '../utils/formatters'

// Cuando un canónico aún tiene múltiples supermarket_products del mismo supermercado,
// la API puede devolver varias entradas con el mismo slug. Se queda con el precio más bajo.
function deduplicatePrices(prices: CurrentPrice[]): CurrentPrice[] {
  const map = new Map<string, CurrentPrice>()
  for (const cp of prices) {
    const existing = map.get(cp.supermarket_slug)
    if (!existing || cp.price < existing.price) {
      map.set(cp.supermarket_slug, cp)
    }
  }
  return Array.from(map.values())
}

interface ProductCardProps {
  product: ProductSummary
}

export default function ProductCard({ product }: ProductCardProps) {
  const navigate = useNavigate()

  return (
    <div
      className="rounded-lg border border-gray-200 bg-white p-4 cursor-pointer hover:shadow-md transition-shadow"
      onClick={() => navigate(`/products/${product.id}`)}
    >
      <h3
        className="font-bold text-gray-900 mb-2 overflow-hidden"
        style={{
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
        }}
      >
        {product.name}
      </h3>

      {product.category && (
        <span className="inline-block rounded-full bg-gray-100 text-gray-600 text-xs px-2 py-0.5 mb-3">
          {product.category}
        </span>
      )}

      <div className="mt-auto">
        {product.current_prices.length === 0 ? (
          <p className="text-gray-400 text-sm">Sin precio disponible</p>
        ) : (
          <ul className="space-y-1">
            {deduplicatePrices(product.current_prices).map((cp) => {
              const isCheapest =
                product.min_price !== null && cp.price === product.min_price
              return (
                <li
                  key={cp.supermarket_slug}
                  className="flex items-center justify-between text-sm"
                >
                  <span className="text-gray-600">{cp.supermarket_name}</span>
                  <span className={isCheapest ? 'font-bold text-green-600' : 'text-gray-400'}>
                    {formatPrice(cp.price)}
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
