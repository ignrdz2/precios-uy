import { useEffect, useState } from 'react'

interface SearchBarProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
}

export default function SearchBar({ value, onChange, placeholder = 'Buscar productos...' }: SearchBarProps) {
  const [inputValue, setInputValue] = useState(value)

  // Sincronizar cuando el prop value cambia externamente
  useEffect(() => {
    setInputValue(value)
  }, [value])

  // Debounce de 500ms antes de propagar el cambio
  useEffect(() => {
    const timer = setTimeout(() => {
      onChange(inputValue)
    }, 500)
    return () => clearTimeout(timer)
  }, [inputValue]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="relative flex items-center">
      <span className="absolute left-3 text-gray-400 pointer-events-none">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <circle cx="6.5" cy="6.5" r="5" stroke="currentColor" strokeWidth="1.5" />
          <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </span>
      <input
        type="text"
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-9 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
      />
      {inputValue.length > 0 && (
        <button
          onClick={() => {
            setInputValue('')
            onChange('')
          }}
          className="absolute right-3 text-gray-400 hover:text-gray-600"
          aria-label="Limpiar búsqueda"
        >
          ✕
        </button>
      )}
    </div>
  )
}
