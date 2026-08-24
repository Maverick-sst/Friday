import { useEffect, useState } from 'react'
import { Shell, type PageId } from './Shell'
import { ConnectStorePage } from './pages/ConnectStorePage'
import { ProfilePage } from './pages/ProfilePage'
import { PoliciesPage } from './pages/PoliciesPage'
import { AgentConsolePage } from './pages/AgentConsolePage'
import { TracePage } from './pages/TracePage'
import type { MerchantSummary } from './lib/types'

export default function App() {
  const [page, setPage] = useState<PageId>('connect')
  const [merchantId, setMerchantId] = useState<string | null>(null)
  const [checked, setChecked] = useState(false)

  // On load: adopt the connected merchant if one exists.
  useEffect(() => {
    fetch('/api/v1/merchants')
      .then((r) => r.json())
      .then((data: { merchants: MerchantSummary[] }) => {
        if (data.merchants.length > 0) {
          setMerchantId(data.merchants[0].id)
        }
      })
      .catch(() => undefined)
      .finally(() => setChecked(true))
  }, [])

  const handleConnected = (slug: string) => {
    setMerchantId(slug)
    setPage('profile')
  }

  const ready = merchantId !== null

  return (
    <Shell page={page} onNavigate={setPage}>
      {!checked && <p className="text-dim">connecting to gateway…</p>}
      {page === 'connect' && <ConnectStorePage onConnected={handleConnected} />}
      {checked && ready && merchantId && page === 'profile' && <ProfilePage merchantId={merchantId} />}
      {checked && ready && merchantId && page === 'policies' && <PoliciesPage merchantId={merchantId} />}
      {checked && ready && merchantId && page === 'console' && (
        <AgentConsolePage key={merchantId} merchantId={merchantId} ready />
      )}
      {checked && ready && merchantId && page === 'trace' && <TracePage merchantId={merchantId} />}
      {checked && !ready && (page === 'profile' || page === 'policies' || page === 'console' || page === 'trace') && (
        <p className="text-warn">No merchant connected yet — start on Connect Store.</p>
      )}
    </Shell>
  )
}
