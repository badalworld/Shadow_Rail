import React from 'react'

interface State { error: Error | null }

/**
 * Last line of defence for the console: a render-time exception in any page
 * must not leave the operator with a white screen while a live position is
 * open.  The panel keeps the crash text (and the reload button) on screen.
 */
export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('dashboard render error', error, info?.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="relative z-10 flex h-screen w-screen items-center justify-center p-6">
        <div className="glass w-full max-w-lg p-6">
          <div className="text-[1.05rem] font-bold accent-text">Dashboard error</div>
          <div className="mt-1 text-[0.74rem] dim">
            A page failed to render. The engine keeps running — this is only the console.
          </div>
          <pre className="scroll-thin mono mt-4 max-h-56 overflow-auto rounded-lg bg-black/30 p-3 text-[0.68rem] text-red-200">
            {String(error?.message || error)}
          </pre>
          <button
            className="chip mt-4 py-2 hover:opacity-80"
            onClick={() => window.location.reload()}
          >
            Reload dashboard
          </button>
        </div>
      </div>
    )
  }
}
