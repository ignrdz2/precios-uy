import { type CompareResponse } from '../api/client'
import { formatPrice } from '../utils/formatters'

interface PriceCompareTableProps {
  compare: CompareResponse
}

export default function PriceCompareTable({ compare }: PriceCompareTableProps) {
  const { comparison, cheapest } = compare

  if (comparison.length === 0) {
    return (
      <p className="text-gray-500 text-sm py-4">
        Sin precios disponibles — ejecuta el scraper primero.
      </p>
    )
  }

  const showDiff = comparison.length > 1
  const minPrice = comparison[0].price // ya ordenado por price ASC

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
            <th className="py-2 pr-4">Supermercado</th>
            <th className="py-2 pr-4">Precio</th>
            {showDiff && <th className="py-2 pr-4">Diferencia</th>}
            <th className="py-2">Ver en sitio</th>
          </tr>
        </thead>
        <tbody>
          {comparison.map((entry) => {
            const isCheapest = entry.supermarket_slug === cheapest
            const diff = entry.price - minPrice
            const pct = minPrice > 0 ? (diff / minPrice) * 100 : 0

            return (
              <tr
                key={entry.supermarket_slug}
                className="border-b border-gray-100 last:border-0"
              >
                <td className="py-3 pr-4 font-medium text-gray-800">
                  {entry.supermarket_name}
                </td>
                <td className="py-3 pr-4 font-bold text-gray-900">
                  {formatPrice(entry.price)}
                </td>
                {showDiff && (
                  <td className="py-3 pr-4">
                    {isCheapest ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-green-100 text-green-700 px-2 py-0.5 text-xs font-medium">
                        ✓ Más barato
                      </span>
                    ) : (
                      <span className="text-red-600">
                        +{formatPrice(diff)} (+{pct.toFixed(1)}%)
                      </span>
                    )}
                  </td>
                )}
                <td className="py-3">
                  {entry.url ? (
                    <a
                      href={entry.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:text-blue-800 hover:underline"
                    >
                      Ver →
                    </a>
                  ) : null}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
