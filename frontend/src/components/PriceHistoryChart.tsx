import { useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getProductHistory, type PriceHistory } from '../api/client'
import { formatDateShort, formatPrice, getSupermarketColor } from '../utils/formatters'
import ErrorMessage from './ErrorMessage'
import LoadingSpinner from './LoadingSpinner'

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function slugToName(slug: string): string {
  return slug
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

interface PriceHistoryChartProps {
  productId: number
}

export default function PriceHistoryChart({ productId }: PriceHistoryChartProps) {
  const [fromDate, setFromDate] = useState(
    toISODate(new Date(Date.now() - 60 * 24 * 60 * 60 * 1000)),
  )
  const [toDate, setToDate] = useState(toISODate(new Date()))
  const [history, setHistory] = useState<PriceHistory | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    getProductHistory(productId, { from: fromDate, to: toDate })
      .then(setHistory)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Error al cargar historial')
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [productId, fromDate, toDate]) // eslint-disable-line react-hooks/exhaustive-deps

  const slugs = history ? Object.keys(history.series) : []
  const hasData = slugs.some((s) => (history?.series[s]?.length ?? 0) > 0)

  const chartData = useMemo(() => {
    if (!history) return []
    const allDates = Array.from(
      new Set(Object.values(history.series).flatMap((points) => points.map((p) => p.date))),
    ).sort()

    return allDates.map((date) => {
      const point: Record<string, string | number> = { date: formatDateShort(date) }
      for (const [slug, points] of Object.entries(history.series)) {
        const match = points.find((p) => p.date === date)
        if (match !== undefined) {
          point[slug] = match.price
        }
      }
      return point
    })
  }, [history])

  return (
    <div className="flex flex-col gap-4">
      {/* Selector de rango */}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-2 text-gray-600">
          Desde
          <input
            type="date"
            value={fromDate}
            max={toDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </label>
        <label className="flex items-center gap-2 text-gray-600">
          Hasta
          <input
            type="date"
            value={toDate}
            min={fromDate}
            onChange={(e) => setToDate(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </label>
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <LoadingSpinner size="sm" />
        </div>
      ) : error ? (
        <ErrorMessage message={error} retry={load} />
      ) : !hasData ? (
        <p className="text-gray-500 text-sm py-4">
          Sin historial de precios disponible para este rango de fechas.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" tick={{ fontSize: 12 }} />
            <YAxis tickFormatter={(v) => `$${v}`} width={70} tick={{ fontSize: 12 }} />
            <Tooltip
              formatter={(value, name) => [formatPrice(Number(value)), slugToName(String(name))]}
            />
            <Legend formatter={(value) => slugToName(String(value))} />
            {slugs.map((slug, i) => (
              <Line
                key={slug}
                type="monotone"
                dataKey={slug}
                name={slug}
                stroke={getSupermarketColor(slug, i)}
                dot={false}
                connectNulls={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
