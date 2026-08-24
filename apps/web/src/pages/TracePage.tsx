import { useCallback, useEffect, useState } from 'react'
import { CircleCheck, CircleX, ScrollText } from 'lucide-react'
import { Panel, StatusPill } from '../components/ui'
import { api } from '../lib/api'
import { formatMinor, timeOf } from '../lib/money'
import type { TransactionTrace, TxnEvent } from '../lib/types'

const EVENT_ICONS: Record<string, 'ok' | 'bad' | 'neutral' | 'money'> = {
  USER_INTENT: 'neutral',
  MERCHANT_DISCOVERED: 'ok',
  PRODUCT_SEARCHED: 'neutral',
  PRODUCT_SELECTED: 'neutral',
  QUOTE_CREATED: 'money',
  POLICY_EVALUATED: 'neutral',
  AUTHORIZATION_GRANTED: 'ok',
  AUTHORIZATION_DENIED: 'bad',
  CART_CREATED: 'neutral',
  PAYMENT_ORDER_CREATED: 'money',
  PAYMENT_AUTHORIZED: 'money',
  PAYMENT_CAPTURED: 'ok',
  PAYMENT_FAILED: 'bad',
  TRANSACTION_BLOCKED: 'bad',
  TRANSACTION_COMPLETED: 'ok',
}

function describeEvent(evt: TxnEvent): string {
  const p = evt.payload ?? {}
  switch (evt.event_type) {
    case 'USER_INTENT':
      return String(p.intent ?? 'user intent recorded')
    case 'MERCHANT_DISCOVERED':
      return `${p.merchant ?? ''} discovered`
    case 'PRODUCT_SELECTED':
      return `selected ${p.product_ref ?? p.product_id ?? ''}`
    case 'QUOTE_CREATED':
      return `live quote ${p.quote_ref ?? ''} · total ${formatMinor(Number(p.total_minor ?? 0))} ${p.currency ?? ''}`
    case 'POLICY_EVALUATED':
      return p.allowed === false
        ? `policy denied [${(p.reason_codes as string[] | undefined)?.join(', ') ?? ''}]`
        : 'policy engine evaluated'
    case 'AUTHORIZATION_GRANTED':
      return `authorized ${formatMinor(Number(p.authorized_minor ?? 0))}`
    case 'AUTHORIZATION_DENIED':
      return `denied · ${(p.reason_codes as string[] | undefined)?.join(', ') ?? ''}`
    case 'CART_CREATED':
      return `cart ${p.cart_ref ?? ''}${p.total_minor ? ` · ${formatMinor(Number(p.total_minor))}` : ''}`
    case 'PAYMENT_ORDER_CREATED':
      return `${p.provider ?? 'payment'} order ${p.order_id ?? ''}`
    case 'PAYMENT_AUTHORIZED':
      return `payment authorized at provider`
    case 'PAYMENT_CAPTURED':
      return `payment captured${p.capture_status ? ` (${p.capture_status})` : ''}`
    case 'PAYMENT_FAILED':
      return `payment failed · ${p.reason ?? ''}`
    case 'TRANSACTION_BLOCKED':
      return `transaction blocked · ${(p.reason_codes as string[] | undefined)?.join(', ') ?? ''}`
    case 'TRANSACTION_COMPLETED':
      return `completed${p.shopify_reference ? ` · source order ${p.shopify_reference}` : ''}`
    default:
      return JSON.stringify(p)
  }
}

export function TracePage({ merchantId }: { merchantId: string }) {
  const [txnRef, setTxnRef] = useState('')
  const [trace, setTrace] = useState<TransactionTrace | null>(null)
  const [events, setEvents] = useState<TxnEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (ref: string) => {
    setLoading(true)
    setError(null)
    try {
      const t = await api.get<TransactionTrace>(`/api/v1/transactions/${ref}`)
      const e = await api.get<{ events: TxnEvent[] }>(`/api/v1/transactions/${ref}/events`)
      setTrace(t)
      setEvents(e.events)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setTrace(null)
      setEvents([])
    } finally {
      setLoading(false)
    }
  }, [])

  // Auto-load the most recent transaction for this merchant on mount.
  useEffect(() => {
    if (!merchantId) return
    fetch(`/api/v1/merchants/${merchantId}/discover`)
      .then(() => undefined) // discovery is cheap; transactions are listed via trace lookup below
      .catch(() => undefined)
  }, [merchantId])

  const blocked = trace?.status === 'BLOCKED' || trace?.status === 'PAYMENT_FAILED'

  return (
    <div className="space-y-5">
      <Panel title="Lookup transaction">
        <div className="flex items-center gap-2">
          <input
            value={txnRef}
            onChange={(e) => setTxnRef(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && load(txnRef.trim())}
            placeholder="txn_XXXXXXXX_XXXXXXXX"
            spellCheck={false}
            className="w-full max-w-md rounded border border-edge bg-[#070b11] px-3 py-2 font-mono text-[12px] outline-none focus:border-cyan/50"
          />
          <button
            onClick={() => txnRef.trim() && load(txnRef.trim())}
            disabled={loading}
            className="rounded border border-cyan/40 bg-cyan/10 px-4 py-2 text-[10px] font-semibold tracking-[0.18em] text-cyan uppercase hover:bg-cyan/20 disabled:opacity-50"
          >
            {loading ? 'loading…' : 'trace'}
          </button>
        </div>
        {error && <p className="mt-2 text-[11px] text-danger">{error}</p>}
      </Panel>

      {trace && (
        <div className="grid gap-5 lg:grid-cols-[300px_1fr]">
          <Panel title="Transaction" tone={blocked ? 'danger' : 'success'}>
            <div className="space-y-2.5 text-[12px]">
              <div className="flex items-center justify-between">
                <span className="text-dim">status</span> <StatusPill status={trace.status} />
              </div>
              <KV k="txn" v={trace.transaction_id} mono />
              <KV k="requested" v={formatMinor(trace.requested_amount_minor, trace.currency)} />
              <KV k="quoted" v={formatMinor(trace.quoted_amount_minor, trace.currency)} />
              <KV k="authorized" v={formatMinor(trace.authorized_amount_minor, trace.currency)} />
              <KV k="final" v={formatMinor(trace.final_amount_minor, trace.currency)} />
              {trace.razorpay_order_id && <KV k="provider order" v={trace.razorpay_order_id} mono />}
              {trace.razorpay_payment_id && <KV k="provider payment" v={trace.razorpay_payment_id} mono />}
              {trace.shopify_reference && <KV k="source order" v={trace.shopify_reference} mono />}
            </div>
          </Panel>

          <Panel title="Audit timeline" right={<ScrollText size={13} className="text-dim" />}>
            <ol className="relative ml-2 space-y-0 border-l border-edge">
              {events.map((evt) => {
                const kind = EVENT_ICONS[evt.event_type] ?? 'neutral'
                const color =
                  kind === 'bad'
                    ? 'text-danger border-danger/50 bg-danger/15'
                    : kind === 'ok'
                      ? 'text-emerald border-emerald/50 bg-emerald/15'
                      : kind === 'money'
                        ? 'text-warn border-warn/40 bg-warn/10'
                        : 'text-mute border-edge-bright bg-panel-2'
                return (
                  <li key={evt.id} className="relative pb-5 pl-6 last:pb-0">
                    <span
                      className={`absolute top-0.5 -left-[9px] flex h-[17px] w-[17px] items-center justify-center rounded-full border ${color}`}
                    >
                      {kind === 'bad' ? <CircleX size={10} /> : kind === 'ok' ? <CircleCheck size={10} /> : null}
                    </span>
                    <div className="flex flex-wrap items-baseline gap-x-3">
                      <span className="font-mono text-[11px] text-dim">{timeOf(evt.timestamp)}</span>
                      <span
                        className={`text-[11px] font-semibold tracking-[0.14em] ${
                          kind === 'bad' ? 'text-danger' : kind === 'ok' ? 'text-emerald' : 'text-cyan'
                        }`}
                      >
                        {evt.event_type.replaceAll('_', ' ')}
                      </span>
                      <span className="text-[10px] tracking-wide text-dim uppercase">via {evt.actor}</span>
                    </div>
                    <p className="mt-0.5 text-[12px] text-ink/85 break-words">{describeEvent(evt)}</p>
                  </li>
                )
              })}
            </ol>
          </Panel>
        </div>
      )}
    </div>
  )
}

function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-b border-edge/50 pb-1.5 last:border-none">
      <span className="shrink-0 text-dim">{k}</span>
      <span className={`truncate text-right ${mono ? 'font-mono text-[11px]' : ''} text-ink/90`}>{v}</span>
    </div>
  )
}
