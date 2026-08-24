import { useEffect, useState } from 'react'
import { Globe, RefreshCw, Tag } from 'lucide-react'
import { JsonViewer, Panel, StatusPill } from '../components/ui'
import { api } from '../lib/api'
import { dateTimeOf } from '../lib/money'
import type { Profile } from '../lib/types'

const CAPABILITY_DOCS: Record<string, string> = {
  discover: 'machine-readable merchant metadata',
  search_products: 'query the synchronized catalog',
  get_product: 'authoritative product + variant detail',
  get_quote: 'live price/inventory snapshot with TTL',
  create_cart: 'transaction context from a quote',
  checkout: 'policy-gated purchase execution',
}

export function ProfilePage({ merchantId }: { merchantId: string }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)

  const load = () => {
    api
      .get<Profile>(`/api/v1/merchants/${merchantId}/profile`)
      .then(setProfile)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    if (merchantId) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [merchantId])

  const resync = async () => {
    setSyncing(true)
    try {
      await api.post(`/api/v1/merchants/${merchantId}/sync`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSyncing(false)
    }
  }

  if (error) return <Panel title="AI-Native Profile" tone="danger">{error}</Panel>
  if (!profile) return <Panel title="AI-Native Profile"><p className="text-dim">loading profile…</p></Panel>

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div className="space-y-5">
        <Panel
          title="Merchant identity"
          right={<StatusPill status={profile.storefront_status ?? 'pending'} />}
        >
          <h2 className="font-[family-name:var(--font-display)] text-xl font-semibold">
            {profile.merchant.name}
          </h2>
          <p className="mt-1 text-mute">{profile.merchant.description ?? 'No description'}</p>
          <dl className="mt-4 space-y-2 text-[12px]">
            <Row icon={<Tag size={12} />} label="category" value={profile.merchant.category ?? '—'} />
            <Row icon={<Globe size={12} />} label="storefront" value={profile.merchant.website ?? '—'} />
            <Row label="source" value={`${profile.source.provider}${profile.source.store_url ? ` · ${profile.source.store_url}` : ''}`} />
            <Row label="last sync" value={dateTimeOf(profile.sync?.last_synced_at ?? profile.source.last_synced_at)} />
            <Row label="catalog size" value={`${profile.sync?.product_count ?? 0} products`} />
          </dl>
          <button
            onClick={resync}
            disabled={syncing}
            className="mt-4 inline-flex items-center gap-2 rounded border border-edge-bright bg-panel-2 px-3 py-1.5 text-[10px] tracking-[0.16em] text-mute uppercase hover:text-cyan hover:border-cyan/40 disabled:opacity-50"
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} /> Re-sync catalog
          </button>
        </Panel>

        <Panel title="Capability contract">
          <ul className="grid gap-2 sm:grid-cols-2">
            {profile.commerce.capabilities.map((cap) => (
              <li key={cap} className="rounded border border-edge bg-panel-2/60 px-3 py-2">
                <p className="text-[11px] text-emerald">▸ {cap}</p>
                <p className="text-[11px] text-dim">{CAPABILITY_DOCS[cap] ?? 'capability'}</p>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[11px] text-dim">
            currency <span className="text-cyan">{profile.commerce.currency}</span> · buyer agents interact only through these capabilities — Shopify specifics never leak through the gateway.
          </p>
        </Panel>
      </div>

      <div className="space-y-5">
        <Panel title="Merchant Agent Profile · machine view" right={<StatusPill status={`v${profile.profile_version}`} />}>
          <JsonViewer data={profile} maxHeight="30rem" />
          <p className="mt-2 text-[11px] text-dim">
            This JSON is what a buyer agent receives from{' '}
            <code className="text-cyan">GET /api/v1/merchants/{merchantId}/discover</code>
          </p>
        </Panel>
      </div>
    </div>
  )
}

function Row({ icon, label, value }: { icon?: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 border-b border-edge/60 pb-1.5 last:border-none">
      <span className="text-dim">{icon}</span>
      <dt className="w-24 shrink-0 text-dim">{label}</dt>
      <dd className="truncate text-ink/90">{value}</dd>
    </div>
  )
}
