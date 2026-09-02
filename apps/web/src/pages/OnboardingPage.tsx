import { useState } from 'react'
import { ArrowRight, CheckCircle2, Clock, Loader2, Radar } from 'lucide-react'
import { team, setMerchant, currentMerchantId } from '../lib/team'
import { Eyebrow, Spinner } from '../components/kit'

const SAMPLE_TASKS = [
  {
    status: 'Working for 2m 14s',
    title: 'Competitor scan: delivery promises',
    snippet: 'Competitor X advertises “arrives by Thursday” site-wide; merchant shows none.',
    when: 'an hour ago',
    tag: null as string | null,
  },
  {
    status: 'Completed in 41s',
    title: 'Buyer simulation: beginner runner',
    snippet: '4 of 5 simulated buyers chose the competitor; friction: no delivery date.',
    when: '3 hours ago',
    tag: null,
  },
  {
    status: 'Scheduled',
    title: 'Daily market signal brief',
    snippet: 'Trend +22% QoQ on category searches; two new D2C entrants detected.',
    when: 'every 9am',
    tag: 'Every 9am',
  },
]

export function OnboardingPage({
  onWorkspace,
}: {
  onWorkspace: (merchantId: string, baselineMissionId?: string) => void
}) {
  const [url, setUrl] = useState('')
  const [goal, setGoal] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setError(null)
    if (!url.trim()) {
      setError('Enter your store URL to begin.')
      return
    }
    setBusy(true)
    try {
      // Re-onboarding an existing store returns the same workspace.
      const existing = currentMerchantId()
      void existing
      const res = await team.onboard(url.trim(), goal.trim() || undefined)
      if (res.merchant_id) setMerchant(res.merchant_id)
      onWorkspace(res.merchant_id, res.mission_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Onboarding failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fade-up">
      <section className="mx-auto max-w-2xl text-center">
        <Eyebrow>always-on ai strategy team</Eyebrow>
        <h1 className="h1 mt-4">
          A persistent AI team that studies your business, so you don't have to guess.
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] text-mute">
          Enter your store's public URL. Your team runs a Day-0 diagnostic — market,
          competitors, buyer simulations, reputation — then keeps working missions from there.
        </p>

        <div className="card mx-auto mt-8 max-w-xl p-5 text-left">
          <label className="mono-data block" htmlFor="store-url">
            store url
          </label>
          <input
            id="store-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://yourstore.com"
            onKeyDown={(e) => e.key === 'Enter' && !busy && submit()}
            className="mt-1.5"
            autoFocus
          />
          <label className="mono-data mt-4 block" htmlFor="goal-text">
            what are you trying to improve? <span className="text-dim">(optional)</span>
          </label>
          <input
            id="goal-text"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="increase revenue · understand competitors · improve AI buyer conversion"
            onKeyDown={(e) => e.key === 'Enter' && !busy && submit()}
            className="mt-1.5"
          />
          {error && <p className="mt-3 text-[12px] text-danger">{error}</p>}
          <button onClick={submit} disabled={busy} className="btn-primary mt-4 w-full">
            {busy ? (
              <>
                <Loader2 size={14} className="animate-spin" /> activating your team…
              </>
            ) : (
              <>
                Analyze My Business <ArrowRight size={14} />
              </>
            )}
          </button>
        </div>
        <p className="mono-data mx-auto mt-3 max-w-md">
          research only by default · every recommendation carries evidence · simulated
          metrics are always labeled
        </p>
      </section>

      {/* Signature element (aside hero-mockup homage): live-looking task feed */}
      <section className="mx-auto mt-14 max-w-2xl">
        <div className="mb-3 flex items-center gap-2 px-1">
          <Radar size={13} className="text-brand" />
          <span className="eyebrow">your team at work</span>
        </div>
        <div className="space-y-2.5">
          {SAMPLE_TASKS.map((t, i) => (
            <article
              key={i}
              className="card flex items-start gap-3 p-4 transition-colors hover:border-edge-strong fade-up"
              style={{ animationDelay: `${i * 90}ms` }}
            >
              <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-brand" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-medium text-ink">{t.title}</span>
                  {t.tag && (
                    <span className="shrink-0 rounded border border-brand/30 bg-brand/10 px-1.5 py-px font-mono text-[9px] uppercase tracking-wide text-brand">
                      {t.tag}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 truncate text-[12px] text-mute">{t.snippet}</p>
                <p className="mono-data mt-1 flex items-center gap-1">
                  <Clock size={10} /> {t.status} · {t.when}
                </p>
              </div>
            </article>
          ))}
          <div className="card flex items-center gap-3 p-4 opacity-60 shimmer">
            <Spinner />
            <span className="text-[12px] text-mute">watching your market…</span>
          </div>
        </div>
      </section>
    </div>
  )
}
