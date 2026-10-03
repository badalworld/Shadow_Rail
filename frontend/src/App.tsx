import React, { useState } from 'react'
import { Layout, type PageKey } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { Scan } from './pages/Scan'
import { Trades } from './pages/Trades'
import { Bots } from './pages/Bots'
import { Logs } from './pages/Logs'
import { Settings } from './pages/Settings'
import { About } from './pages/About'

export const App: React.FC = () => {
  const [page, setPage] = useState<PageKey>('dashboard')
  return (
    <>
      {/* liquid animated backdrop (CSS only — no repaint cost on the data path) */}
      <div className="sr-backdrop">
        <div className="sr-grid" />
        <div className="sr-blob b1" />
        <div className="sr-blob b2" />
        <div className="sr-blob b3" />
        <div className="sr-scan" />
        <div className="sr-noise" />
      </div>

      <Layout page={page} setPage={setPage}>
        {page === 'dashboard' && <Dashboard goTo={setPage} />}
        {page === 'scan' && <Scan />}
        {page === 'trades' && <Trades />}
        {page === 'bots' && <Bots />}
        {page === 'logs' && <Logs />}
        {page === 'settings' && <Settings />}
        {page === 'about' && <About />}
      </Layout>
    </>
  )
}

export default App
