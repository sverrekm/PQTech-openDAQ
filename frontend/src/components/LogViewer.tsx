import { useCallback, useRef, useEffect } from 'react'
import { fetchLogg } from '../api/logg'
import { usePolling } from '../hooks/usePolling'

export default function LogViewer() {
  const fetcher = useCallback(() => fetchLogg(200), [])
  const { data, loading } = usePolling(fetcher, 5000)
  const preRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight
    }
  }, [data])

  const lines = data?.linjer ?? []

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">Logg</h2>
      {loading && !data ? (
        <div className="flex justify-center items-center h-24">
          <svg className="animate-spin h-6 w-6 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
        </div>
      ) : data?.feil ? (
        <p className="text-gray-500 text-sm">{data.feil}</p>
      ) : (
        <pre ref={preRef} className="bg-gray-800 border border-gray-700 rounded-lg p-3 text-xs text-green-400 max-h-72 overflow-y-auto whitespace-pre-wrap font-mono">
          {lines.length > 0 ? lines.join('\n') : '(ingen logg-data)'}
        </pre>
      )}
    </div>
  )
}
