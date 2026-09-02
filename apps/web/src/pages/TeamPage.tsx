import { useCallback, useEffect, useState } from 'react'
import { Bot, Plus, ShoppingBag } from 'lucide-react'
import { team, currentMerchantId, type MissionSummary } from '../lib/team'
import { Empty, Panel, SectionHead, StatusChip } from '../components/kit'

const AGENTS = [
  { key: 'market', name: 'Market Intelligence', blurb: 'Category trends, new entrants, opportunities and threats.' },
  { key: 'competitor', name: 'Competitor Intelligence', blurb: 'Pricing, positioning, product changes, review sentiment.' },
  { key: 'buyer', name: 'AI Buyer Simulation', blurb: 'Realistic buyers run purchase missions and report friction.' },
  { key: 'presence', name: 'Digital Presence', blurb: 'Reviews, community, press — how the public sees you.' },
  { key: 'reviews', name: 'Reviews & Community', blurb: 'Reddit, YouTube and social voice-of-customer themes.' },
  { key: 'ads', name: 'Ads & Promotions', blurb: 'Active social ads, offers and creative messaging — yours and competitors.' },
  { key: 'catalog', name: 'Catalog Scan', blurb: 'What is actually sold, at what price, with what ratings.' },
  { key: 'strategy', name: 'Strategy', blurb: 'Synthesizes evidence into ranked, actionable recommendations.' },
]

const DEFAULT_OBJECTIVE = 'Why is Competitor X outperforming us for beginner customers?'

const BUYER_MISSION_DEFAULT =
  "Buy a typical product from this store's main category, comparing alternatives before deciding."

export function TeamPage({
  merchantId,
  onOpenMission,
}: {
  merchantId: string
  onOpenMission: (missionId: string) => void
}) {
  const [missions, setMissions] = useState<MissionSummary[]>([])
  const [objective, setObjective] = useState(DEFAULT_OBJECTIVE)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [buyerMission, setBuyerMission] = useState(BUYER_MISSION_DEFAULT)
  const [buyerBusy, setBuyerBusy] = useState(false)
  const [buyerError, setBuyerError] = useState<string | null>(null)
  const [shopping, setShopping] = useState({ product: '', size: '', color: '', brand: '', budget: '' })
  const [shopBusy, setShopBusy] = useState(false)
  const [shopError, setShopError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    team
      .listMissions(merchantId)
      .then(setMissions)
      .catch(() => {})
  }, [merchantId])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 4000)
    return () => clearInterval(t)
  }, [refresh])

  const launch = async () => {
    setError(null)
    setBusy(true)
    try {
      await team.createMission({
        merchant_id: merchantId,
        name: objective.slice(0, 80),
        objective,
        mission_type: 'on_demand',
      })
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to launch mission')
    } finally {
      setBusy(false)
    }
  }

  const active = missions.filter((m) => ['RUNNING', 'QUEUED'].includes(m.status))
  const done = missions.filter((m) => !['RUNNING', 'QUEUED'].includes(m.status))

  const simulateBuyer = async () => {
    setBuyerError(null)
    setBuyerBusy(true)
    try {
      const res = await team.createMission({
        merchant_id: merchantId,
        name: `Buyer sim — ${buyerMission.slice(0, 60)}`,
        objective: buyerMission,
        mission_type: 'buyer_sim',
        agent_assignments: ['buyer'],
        budget_runs: 3,
      })
      setBuyerMission(BUYER_MISSION_DEFAULT)
      refresh()
      if (res.mission_id) onOpenMission(res.mission_id)
    } catch (e) {
      setBuyerError(e instanceof Error ? e.message : 'Failed to launch buyer simulation')
    } finally {
      setBuyerBusy(false)
    }
  }

  const launchShopping = async () => {
    setShopError(null)
    setShopBusy(true)
    const spec: Record<string, unknown> = { ...shopping }
    const objective = `Buy me ${shopping.product || 'a product'}${
      shopping.brand ? ` by ${shopping.brand}` : ''
    }${shopping.size ? ` in size ${shopping.size}` : ''}${
      shopping.color ? ` in ${shopping.color}` : ''
    }${shopping.budget ? ` under INR ${shopping.budget}` : ''}. Go to checkout.`
    try {
      const res = await team.createMission({
        merchant_id: merchantId,
        name: `Shop — ${(shopping.product || 'product').slice(0, 60)}`,
        objective,
        mission_type: 'shopping',
        budget_runs: 2,
        shopping: spec,
      })
      setShopping({ product: '', size: '', color: '', brand: '', budget: '' })
      refresh()
      if (res.mission_id) onOpenMission(res.mission_id)
    } catch (e) {
      setShopError(e instanceof Error ? e.message : 'Failed to launch shopping')
    } finally {
      setShopBusy(false)
    }
  }

  return (
    <div className="fade-up space-y-10">
      {/* The five agents */}
      <section>
        <SectionHead
          eyebrow="your ai team"
          title="Eight specialists. One shared memory."
          support="Each agent has its own tools and mission types; lightweight scouts deep-dive signals mid-mission; Strategy reads everyone's evidence."
        />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {AGENTS.map((a, i) => {
            const busyCount = active.length
            return (
              <article
                key={a.key}
                className="card p-4 transition-colors hover:border-edge-strong fade-up"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <div className="flex items-center justify-between">
                  <span className="flex h-8 w-8 items-center justify-center rounded-md bg-brand/10 text-brand">
                    <Bot size={15} />
                  </span>
                  {busyCount > 0 && (
                    <span className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-wide text-brand">
                      <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-current" /> active
                    </span>
                  )}
                </div>
                <h3 className="display mt-3 text-[14px] font-semibold text-ink">{a.name}</h3>
                <p className="mt-1 text-[12px] leading-relaxed text-mute">{a.blurb}</p>
              </article>
            )
          })}
        </div>
      </section>

      {/* Launch mission */}
      <section>
        <SectionHead
          eyebrow="launch a mission"
          title="Give the team an objective."
          support="A mission is a bounded unit of work with a budget. Agents gather evidence; Strategy closes with ranked recommendations."
        />
        <Panel>
          <textarea
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            rows={2}
            className="resize-none"
            placeholder="e.g. Why is Competitor X winning beginner runners?"
          />
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="mono-data">default fleet: market · competitor · presence · reviews → strategy</span>
            <button onClick={launch} disabled={busy || !currentMerchantId()} className="btn-primary shrink-0">
              <Plus size={14} /> {busy ? 'queuing…' : 'Launch mission'}
            </button>
          </div>
          {error && <p className="mt-2 text-[12px] text-danger">{error}</p>}
        </Panel>
      </section>

      {/* Buyer simulation quick action: single agent, no strategy pass */}
      <section>
        <SectionHead
          eyebrow="buyer lab"
          title="Simulate buyers — no full pipeline."
          support="Runs only the AI buyer against the live storefront: memory → product discovery → quote → cart → checkout → policy → payment. Fast enough to iterate on friction."
        />
        <Panel>
          <textarea
            value={buyerMission}
            onChange={(e) => setBuyerMission(e.target.value)}
            rows={2}
            className="resize-none"
            placeholder="e.g. Buy running shoes under ₹5,000 and report every point of friction."
          />
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="mono-data">single agent · buyer only · no strategy pass</span>
            <button
              onClick={simulateBuyer}
              disabled={buyerBusy || !currentMerchantId()}
              className="btn-primary shrink-0"
            >
              <Bot size={14} /> {buyerBusy ? 'queuing…' : 'Simulate buyers'}
            </button>
          </div>
          {buyerError && <p className="mt-2 text-[12px] text-danger">{buyerError}</p>}
        </Panel>
      </section>

      {/* Shopping mission (B7): direct, browser-first checkout on your spec */}
      <section>
        <SectionHead
          eyebrow="direct shopping"
          title="Tell the agent exactly what to buy."
          support="Give your product/​size/​color/​budget — the agent parses it, drives a managed stealth browser straight to it, materializes the offer and moves to checkout. No broad research."
        />
        <Panel>
          <div className="grid gap-3 sm:grid-cols-2">
            <input
              value={shopping.product}
              onChange={(e) => setShopping({ ...shopping, product: e.target.value })}
              placeholder="Product (e.g. Ultraboost 5)"
              className="w-full"
            />
            <input
              value={shopping.brand}
              onChange={(e) => setShopping({ ...shopping, brand: e.target.value })}
              placeholder="Brand (e.g. adidas)"
              className="w-full"
            />
            <input
              value={shopping.size}
              onChange={(e) => setShopping({ ...shopping, size: e.target.value })}
              placeholder="Size (e.g. 9)"
              className="w-full"
            />
            <input
              value={shopping.color}
              onChange={(e) => setShopping({ ...shopping, color: e.target.value })}
              placeholder="Color (e.g. Black)"
              className="w-full"
            />
            <input
              value={shopping.budget}
              onChange={(e) => setShopping({ ...shopping, budget: e.target.value })}
              placeholder="Budget INR (e.g. 12000)"
              className="w-full"
            />
          </div>
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="mono-data">browser-first · no research fleet · goes to checkout</span>
            <button onClick={launchShopping} disabled={shopBusy || !currentMerchantId()} className="btn-primary shrink-0">
              <ShoppingBag size={14} /> {shopBusy ? 'queuing…' : 'Shop now'}
            </button>
          </div>
          {shopError && <p className="mt-2 text-[12px] text-danger">{shopError}</p>}
        </Panel>
      </section>

      {/* Mission lists */}
      {active.length > 0 && (
        <section>
          <SectionHead eyebrow="in flight" title="Active missions" />
          <ul className="space-y-2">
            {active.map((m) => (
              <li key={m.id}>
                <MissionRow mission={m} onOpen={() => onOpenMission(m.id)} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <SectionHead eyebrow="history" title="Completed missions" />
        {done.length === 0 ? (
          <Empty>No completed missions yet — the baseline lands here first.</Empty>
        ) : (
          <ul className="space-y-2">
            {done.map((m) => (
              <li key={m.id}>
                <MissionRow mission={m} onOpen={() => onOpenMission(m.id)} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function MissionRow({ mission, onOpen }: { mission: MissionSummary; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="card flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:border-edge-strong"
    >
      <StatusChip status={mission.status} />
      <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{mission.name}</span>
      <span className="mono-data hidden shrink-0 sm:block">
        {new Date(mission.created_at).toLocaleString()}
      </span>
    </button>
  )
}
