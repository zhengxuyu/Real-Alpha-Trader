import React from 'react'

interface ErrorBoundaryProps {
  children: React.ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  errorMsg?: string
  componentStack?: string
  appState?: any
}

export default class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: unknown): Partial<ErrorBoundaryState> {
    return { hasError: true, errorMsg: error instanceof Error ? error.message : String(error) }
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    // Read latest app-state snapshot if available
    const appState = (window as any).__APP_STATE_DEBUG__
    this.setState({ componentStack: info.componentStack, appState })
    // Log rich details to console for production diagnosis
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] Caught error:', error)
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] Component stack:', info.componentStack)
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] App state snapshot:', appState)
  }

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 16 }}>
          <h2 style={{ fontWeight: 600, marginBottom: 8 }}>Oops, something went wrong.</h2>
          <div style={{ fontSize: 12, color: 'var(--muted-foreground, #666)' }}>
            <div>Message: {this.state.errorMsg}</div>
            {this.state.componentStack && (
              <pre style={{ whiteSpace: 'pre-wrap', marginTop: 8 }}>{this.state.componentStack}</pre>
            )}
            {this.state.appState && (
              <details style={{ marginTop: 8 }}>
                <summary>Runtime state snapshot</summary>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(this.state.appState, null, 2)}</pre>
              </details>
            )}
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

