interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg'
}

const SIZE_PX = { sm: 16, md: 32, lg: 48 }

export default function LoadingSpinner({ size = 'md' }: LoadingSpinnerProps) {
  const px = SIZE_PX[size]
  return (
    <div className="flex items-center justify-center">
      <svg
        className="animate-spin text-blue-600"
        width={px}
        height={px}
        viewBox="0 0 24 24"
        fill="none"
        aria-label="Cargando"
      >
        <circle
          className="opacity-25"
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="4"
        />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
        />
      </svg>
    </div>
  )
}
