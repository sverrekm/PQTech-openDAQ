import { Component, type ReactNode, type ErrorInfo } from 'react'

interface Props {
  children: ReactNode
}
interface State {
  error: Error | null
  info: string
}

/** Fangar render-feil så appen viser ei lesbar feilmelding i staden for ein
 *  blank kvit side. Viser feil + komponent-stack, og ein reload-knapp. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ info: info.componentStack || '' })
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary fanga:', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="min-h-screen bg-[#111] text-gray-200 p-6 overflow-auto">
        <div className="max-w-2xl mx-auto bg-[#1a1a1a] border border-red-700 rounded-lg p-5">
          <h1 className="text-lg font-semibold text-red-400 mb-2">Noko gjekk gale i grensesnittet</h1>
          <p className="text-sm text-gray-400 mb-4">
            Sida krasja under teikning. Detaljane under hjelper med feilsøking.
          </p>
          <pre className="text-xs text-red-300 whitespace-pre-wrap break-words bg-black/40 rounded p-3 mb-3">
            {String(this.state.error?.stack || this.state.error?.message || this.state.error)}
          </pre>
          {this.state.info && (
            <pre className="text-xs text-gray-500 whitespace-pre-wrap break-words bg-black/40 rounded p-3 mb-4 max-h-48 overflow-auto">
              {this.state.info}
            </pre>
          )}
          <button
            onClick={() => window.location.reload()}
            className="bg-[#D76428] hover:bg-[#B85420] text-white text-sm font-medium py-2 px-4 rounded-lg"
          >
            Last inn på nytt
          </button>
        </div>
      </div>
    )
  }
}
