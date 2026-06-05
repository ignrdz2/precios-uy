import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getHealth, type HealthResponse } from '../api/client'
import { formatDate } from '../utils/formatters'

interface LayoutProps {
  children: React.ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const [health, setHealth] = useState<HealthResponse | null>(null)

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => {})
  }, [])

  return (
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-50 bg-white shadow-sm">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-3">
          <Link to="/" className="font-bold text-lg text-blue-700 hover:text-blue-900">
            uy-precios
          </Link>
          <span className="hidden sm:block text-sm text-gray-500">
            Comparador de precios Uruguay
          </span>
        </div>
      </header>

      <main className="flex-1 max-w-5xl mx-auto w-full px-4 py-6">{children}</main>

      <footer className="border-t border-gray-100 bg-gray-50">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-3 text-sm text-gray-500">
          {health ? (
            <>
              {health.database !== 'connected' && (
                <span className="inline-flex items-center gap-1 rounded-full bg-red-100 text-red-700 px-2 py-0.5 text-xs font-medium">
                  ⚠ Base de datos sin conexión
                </span>
              )}
              {health.last_scrape?.finished_at ? (
                <span>Última actualización: {formatDate(health.last_scrape.finished_at)}</span>
              ) : (
                <span>Sin datos de scrape aún</span>
              )}
            </>
          ) : null}
        </div>
      </footer>
    </div>
  )
}
