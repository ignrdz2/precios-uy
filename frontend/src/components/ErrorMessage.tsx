interface ErrorMessageProps {
  message: string
  retry?: () => void
}

export default function ErrorMessage({ message, retry }: ErrorMessageProps) {
  return (
    <div className="rounded-md bg-red-50 border border-red-200 p-4 flex flex-col gap-3">
      <div className="flex items-start gap-2">
        <span aria-hidden="true">⚠️</span>
        <p className="text-red-800 text-sm">{message}</p>
      </div>
      {retry && (
        <button
          onClick={retry}
          className="self-start text-sm font-medium text-red-700 underline hover:text-red-900"
        >
          Reintentar
        </button>
      )}
    </div>
  )
}
