import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Link2, Loader2, ShieldCheck, Store, Zap } from 'lucide-react'
import { Panel } from '../components/ui'
import { api } from '../lib/api'

type ConnectState = 'idle' | 'detecting' | 'redirecting' | 'connected' | 'failed'

const STEPS = [
  { key: 'detecting', label: 'Detect Shopify platform', detail: 'validating *.myshopify.com host' },
  { key: 'redirecting', label: 'Authorization pending', detail: 'merchant grants read_products, read_inventory, write_draft_orders' },
  { key: 'syncing', label: 'Catalog sync', detail: 'products → canonical commerce model → agent profile' },
  { key: 'connected', label: 'Agent commerce enabled', detail: 'merchant is discoverable by buyer agents' },
] as const

export function ConnectStorePage({ onConnected }: { onConnected: (slug: string) => void }) {
  const [storeUrl, setStoreUrl] = useState('')
  const [state, setState] = useState<ConnectState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [activeStep, setActiveStep] = useState<string | null>(null)
  const [, setSeededSlug] = useState<string | null>(null)

  useEffect(() => {
    // Reflect OAuth callback results (?connected=slug).
    const params = new URLSearchParams(window.location.search)
    const connected = params.get('connected')
    if (connected) {
      setSeededSlug(connected)
      setState('connected')
      onConnected(connected)
    }
  }, [onConnected])

  const connect = useCallback(async () => {
    if (!storeUrl.trim()) return
    setError(null)
    setState('detecting')
    setActiveStep('detecting')
    try {
      await new Promise((r) => setTimeout(r, 450)) // let the state machine render
      const res = await api.post<{ authorize_url: string; shop: string }>('/api/v1/onboarding/shopify/connect', {
        store_url: storeUrl.trim(),
      })
      setActiveStep('redirecting')
      setState('redirecting')
      await new Promise((r) => setTimeout(r, 450))
      window.location.href = res.authorize_url
    } catch (e) {
      setState('failed')
      setError(e instanceof Error ? e.message : String(e))
      setActiveStep(null)
    }
  }, [storeUrl])

  const demoSeed = useCallback(async () => {
    setError(null)
    try {
      const res = await api.post<{ merchant_id: string; created: boolean }>('/api/v1/onboarding/demo-seed')
      setSeededSlug(res.merchant_id)
      setActiveStep('connected')
      setState('connected')
      onConnected(res.merchant_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [onConnected])

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div className="space-y-5">
        <Panel title="Connect a Shopify store" tone={state === 'failed' ? 'danger' : 'default'}>
          <p className="mb-4 text-mute">
            Enter the store's <span className="text-cyan">*.myshopify.com</span> domain. The merchant stays
            exactly as-is — we generate an agent-native interface alongside the human storefront.
          </p>
          <label className="mb-1.5 block text-[10px] tracking-[0.16em] text-dim uppercase">Store domain</label>
          <div className="flex items-center gap-2 rounded border border-edge bg-[#070b11] px-3 focus-within:border-emerald/50">
            <Store size={14} className="text-dim" />
            <input
              value={storeUrl}
              onChange={(e) => setStoreUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && connect()}
              placeholder="your-store.myshopify.com"
              spellCheck={false}
              className="w-full bg-transparent py-2.5 font-mono text-[12px] outline-none placeholder:text-dim/70"
            />
          </div>
          <button
            onClick={connect}
            disabled={state === 'detecting' || state === 'redirecting' || !storeUrl.trim()}
            className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded border border-emerald/40 bg-emerald/10 py-2.5 text-[11px] font-semibold tracking-[0.18em] text-emerald uppercase transition-colors hover:bg-emerald/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {state === 'detecting' || state === 'redirecting' ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Zap size={13} />
            )}
            Make AI-Native
          </button>

          {error && (
            <p className="mt-3 rounded border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
              {error}
            </p>
          )}

          <div className="my-4 flex items-center gap-3 text-dim">
            <span className="h-px flex-1 bg-edge" />
            <span className="text-[10px] tracking-[0.2em] uppercase">no store yet?</span>
            <span className="h-px flex-1 bg-edge" />
          </div>

          <button
            onClick={demoSeed}
            className="inline-flex w-full items-center justify-center gap-2 rounded border border-edge-bright bg-panel-2 py-2.5 text-[11px] tracking-[0.18em] text-mute uppercase transition-colors hover:text-ink hover:border-cyan/40 hover:text-cyan"
          >
            <Link2 size={13} /> Load demo merchant (Velocity Sports)
          </button>
        </Panel>
      </div>

      <div className="space-y-5">
        <Panel title="Connection pipeline" right={state === 'connected' ? <CheckCircle2 size={14} className="text-emerald" /> : undefined}>
          <ol className="space-y-0">
            {STEPS.map((step, i) => {
              const reached = activeStep !== null || state === 'connected'
              const isActive =
                (state === 'detecting' && step.key === 'detecting') ||
                (state === 'redirecting' && step.key === 'redirecting') ||
                (state === 'connected' && step.key === 'connected')
              const done =
                state === 'connected'
                  ? true
                  : STEPS.findIndex((s) => s.key === activeStep) > i
              return (
                <li key={step.key} className="relative flex gap-3 pb-5 last:pb-0">
                  {i < STEPS.length - 1 && (
                    <span
                      className={`absolute top-6 left-[9px] h-full w-px ${done ? 'bg-emerald/40' : 'bg-edge'}`}
                    />
                  )}
                  <span
                    className={`z-10 mt-0.5 flex h-[19px] w-[19px] shrink-0 items-center justify-center rounded-full border text-[9px] ${
                      done || isActive
                        ? 'border-emerald/60 bg-emerald/15 text-emerald'
                        : reached
                          ? 'border-edge-bright bg-panel text-dim'
                          : 'border-edge bg-panel text-dim'
                    }`}
                  >
                    {done || isActive ? <CheckCircle2 size={12} /> : i + 1}
                  </span>
                  <div>
                    <p className={`text-[12px] ${isActive ? 'text-ink' : done ? 'text-ink/80' : 'text-dim'}`}>
                      {step.label}
                    </p>
                    <p className="text-[11px] text-dim">{step.detail}</p>
                  </div>
                </li>
              )
            })}
          </ol>
        </Panel>

        <Panel title="Security posture" tone="success">
          <ul className="space-y-2 text-[12px] text-mute">
            <li className="flex gap-2"><ShieldCheck size={14} className="mt-0.5 shrink-0 text-emerald" />
              Access tokens are encrypted server-side — never exposed to agents or browsers</li>
            <li className="flex gap-2"><ShieldCheck size={14} className="mt-0.5 shrink-0 text-emerald" />
              Buyer agents see only the canonical gateway capabilities</li>
            <li className="flex gap-2"><ShieldCheck size={14} className="mt-0.5 shrink-0 text-emerald" />
              Every checkout passes the deterministic policy engine before any payment call</li>
          </ul>
        </Panel>
      </div>
    </div>
  )
}
