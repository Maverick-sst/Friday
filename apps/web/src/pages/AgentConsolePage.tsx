import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, CreditCard, Loader2, PlayCircle, ShieldAlert, TerminalSquare } from 'lucide-react'
import { Panel } from '../components/ui'
import { api } from '../lib/api'
import { formatMinor } from '../lib/money'
import type { AgentEventPayload, PaymentInitiation } from '../lib/types'

const DEFAULT_INTENT =
  'Find me Nike Downshifter 14, size 9, under INR 5,000, with reliable returns. Buy it for me.'

const SCENARIOS = [
  { value: '', label: 'none — happy path' },
  { value: 'PRICE_CHANGE_AFTER_QUOTE', label: 'price change after quote (₹4,799 → ₹5,799)' },
  { value: 'INVENTORY_RACE', label: 'inventory race (size vanishes)' },
]

interface Line {
  kind: 'call' | 'result' | 'status' | 'final' | 'error' | 'raw'
  text: string
  tone?: 'ok' | 'bad' | 'warn'
}

export function AgentConsolePage({
  merchantId,
  ready,
}: {
  merchantId: string
  ready: boolean
}) {
  const [intent, setIntent] = useState(DEFAULT_INTENT)
  const [budget, setBudget] = useState('5000')
  const [scenario, setScenario] = useState('')
  const [running, setRunning] = useState(false)
  const [lines, setLines] = useState<Line[]>([])
  const [lastTxnRef, setLastTxnRef] = useState<string | null>(null)
  const [awaitingPayment, setAwaitingPayment] = useState<PaymentInitiation | null>(null)
  const consoleRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    consoleRef.current?.scrollTo({ top: consoleRef.current.scrollHeight })
  }, [lines])

  const pushLine = useCallback((line: Line) => setLines((prev) => [...prev, line]), [])

  const run = async () => {
    if (!ready || running) return
    setRunning(true)
    setLines([])
    setLastTxnRef(null)
    setAwaitingPayment(null)
    pushLine({ kind: 'raw', text: `$ agent.run --merchant ${merchantId} --budget ₹${budget}` })

    try {
      // Reset any prior failure-demo overrides so scenarios start clean.
      await api.post(`/api/v1/demo/reset?merchant_id=${merchantId}`)

      const session = await api.post<{ session_id: string }>('/api/v1/agent/sessions', {
        intent,
        max_budget_minor: Math.round(Number(budget) * 100),
        currency: 'INR',
        demo_scenario: scenario || null,
      })
      rememberSession(session.session_id)

      const res = await fetch(`/api/v1/agent/sessions/${session.session_id}/run`)
      if (!res.body) throw new Error('no SSE stream')
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() ?? ''
        for (const chunk of chunks) {
          if (!chunk.startsWith('data: ')) continue
          const event = JSON.parse(chunk.slice(6)) as AgentEventPayload
          consumeAgentEvent(event, pushLine, setAwaitingPayment, setLastTxnRef)
        }
      }
    } catch (e) {
      pushLine({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
      <div className="space-y-5">
        <Panel title="Buyer mission">
          <label className="mb-1.5 block text-[10px] tracking-[0.16em] text-dim uppercase">
            user intent
          </label>
          <textarea
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            rows={3}
            className="w-full resize-none rounded border border-edge bg-[#070b11] px-3 py-2 font-mono text-[12px] outline-none focus:border-cyan/50"
          />

          <div className="mt-3 grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1.5 block text-[10px] tracking-[0.16em] text-dim uppercase">
                budget (₹)
              </label>
              <input
                type="number"
                min={100}
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                className="w-full rounded border border-edge bg-[#070b11] px-3 py-2 font-mono text-[13px] outline-none focus:border-cyan/50"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-[10px] tracking-[0.16em] text-dim uppercase">
                demo scenario
              </label>
              <select
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                className="w-full rounded border border-edge bg-[#070b11] px-2 py-2.5 font-mono text-[11px] outline-none focus:border-cyan/50"
              >
                {SCENARIOS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <button
            onClick={run}
            disabled={!ready || running}
            className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded border border-emerald/40 bg-emerald/10 py-2.5 text-[11px] font-semibold tracking-[0.2em] text-emerald uppercase hover:bg-emerald/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {running ? <Loader2 size={14} className="animate-spin" /> : <PlayCircle size={14} />}
            {running ? 'agent running…' : 'Run Buyer Agent'}
          </button>

          {!ready && (
            <p className="mt-3 text-[11px] text-warn">
              Connect or load the demo merchant first.
            </p>
          )}
        </Panel>

        <Panel title="Authorization envelope" tone="success">
          <ul className="space-y-1.5 text-[11.5px] text-mute">
            <li>buyer limit · <span className="text-emerald">{formatMinor(Math.round(Number(budget) * 100))}</span></li>
            <li>effective cap · <span className="text-emerald">min(merchant policy, buyer limit)</span></li>
            <li>currency · <span className="text-emerald">INR</span></li>
            <li>authorization window · <span className="text-emerald">30 minutes</span></li>
          </ul>
          <p className="mt-3 flex gap-1.5 text-[11px] text-dim">
            <ShieldAlert size={13} className="mt-0.5 shrink-0 text-warn" />
            The LLM never sees these numbers as editable inputs — the gateway derives them server-side.
          </p>
        </Panel>
      </div>

      <div className="space-y-5">
        <Panel
          title="Live agent trace"
          right={
            running && (
              <span className="flex items-center gap-1.5 text-[10px] tracking-widest text-cyan uppercase">
                <TerminalSquare size={12} /> streaming
              </span>
            )
          }
          tone={lines.some((l) => l.tone === 'bad') ? 'danger' : lines.some((l) => l.kind === 'final' && l.tone === 'ok') ? 'success' : 'default'}
        >
          <div ref={consoleRef} className="h-[420px] space-y-1 overflow-auto rounded border border-edge bg-[#070b11] p-3 font-mono text-[11.5px] leading-relaxed">
            {lines.length === 0 && (
              <p className="text-dim">// press “Run Buyer Agent” to begin an autonomous purchase</p>
            )}
            {lines.map((line, i) => (
              <p
                key={i}
                className={
                  line.kind === 'raw'
                    ? 'text-dim'
                    : line.kind === 'call'
                      ? 'text-cyan before:mr-2 before:text-dim before:content-["▸"]'
                      : line.kind === 'final'
                        ? line.tone === 'ok'
                          ? 'mt-2 border-t border-edge pt-2 font-semibold text-emerald'
                          : 'mt-2 border-t border-edge pt-2 font-semibold text-danger'
                        : line.tone === 'bad'
                          ? 'text-danger'
                          : line.tone === 'warn'
                            ? 'text-warn'
                            : 'text-ink/85 after:content-[""]'
                }
              >
                {line.text}
                {running && i === lines.length - 1 && <span className="cursor-blink" />}
              </p>
            ))}
            {awaitingPayment && (
              <MockCheckoutSheet initiation={awaitingPayment} onComplete={() => setAwaitingPayment(null)} />
            )}
          </div>
        </Panel>

        {lastTxnRef && !running && (
          <Panel title="Follow this transaction">
            <p className="text-[12px] text-mute">
              Full audit trail for <span className="font-mono text-cyan">{lastTxnRef}</span> is available in{' '}
              <a href="#trace" className="text-emerald underline decoration-dotted">Transaction Trace</a>.
            </p>
          </Panel>
        )}
      </div>
    </div>
  )
}

function consumeAgentEvent(
  event: AgentEventPayload,
  push: (l: Line) => void,
  setAwaitingPayment: (p: PaymentInitiation | null) => void,
  setLastTxnRef: (ref: string | null) => void,
): void {
  if (event.type === 'tool_call') {
    push({ kind: 'call', text: `${event.tool}() — ${event.label}` })
    return
  }
  if (event.type === 'tool_result') {
    const r = event.payload?.result
    if (r?.blocked) {
      push({ kind: 'result', text: event.label, tone: 'bad' })
      if (r.explanation) push({ kind: 'result', text: `↳ ${r.explanation}`, tone: 'bad' })
      if (r.transaction_id) setLastTxnRef(r.transaction_id)
      return
    }
    if (r?.status === 'PAYMENT_PENDING' && r.payment_initiation) {
      push({ kind: 'result', text: event.label, tone: 'ok' })
      setLastTxnRef(r.transaction_id ?? null)
      setAwaitingPayment(r.payment_initiation)
      return
    }
    push({ kind: 'result', text: event.label })
    return
  }
  if (event.type === 'final') {
    const blocked = event.payload?.outcome === 'BLOCKED'
    push({ kind: 'final', text: event.label, tone: blocked ? 'bad' : 'ok' })
    return
  }
  if (event.type === 'error') {
    push({ kind: 'error', text: event.label, tone: 'bad' })
    return
  }
  push({ kind: 'status', text: event.label })
}

function MockCheckoutSheet({
  initiation,
  onComplete,
}: {
  initiation: PaymentInitiation
  onComplete: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const isReal = initiation.provider === 'razorpay'

  const payWithRazorpay = useCallback(async () => {
    setBusy(true)
    try {
      await loadRazorpayScript()
      const rzp = new (window as unknown as RazorpayGlobal).Razorpay({
        key: initiation.key_id,
        amount: initiation.amount_minor,
        currency: initiation.currency,
        name: 'Agent Commerce Gateway',
        description: `Order ${initiation.order_id}`,
        order_id: initiation.order_id,
        prefill: { name: 'Demo Buyer', email: 'buyer@agents.test', contact: '+91 90000 00000' },
        theme: { color: '#34d399' },
        handler: async (response: RazorpayResponse) => {
          const res = await api.post<{ status: string; transaction_id: string }>(
            `/api/v1/transactions/${initiation.txn_ref}/payment/complete`,
            {
              order_id: response.razorpay_order_id,
              payment_id: response.razorpay_payment_id,
              signature: response.razorpay_signature,
              session_id: currentSessionId(),
            },
          )
          setDone(res.status)
          onComplete()
        },
        modal: { ondismiss: () => setBusy(false) },
      })
      rzp.open()
    } finally {
      setBusy(false)
    }
  }, [initiation, onComplete])

  const completeMock = async () => {
    setBusy(true)
    try {
      const res = await api.post<{ status: string; transaction_id: string; reason?: string }>(
        `/api/v1/transactions/${initiation.txn_ref}/payment/complete`,
        {
          order_id: initiation.order_id,
          payment_id: `pay_${initiation.order_id}`,
          signature: 'mock-signature',
          session_id: currentSessionId(),
        },
      )
      setDone(res.status)
      onComplete()
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <p className="mt-2 rounded border border-emerald/30 bg-emerald/10 p-2.5 text-emerald">
        payment settled · status {done} · see Transaction Trace ↗
      </p>
    )
  }

  return (
    <div className="my-3 rounded border border-warn/30 bg-warn/5 p-3">
      <p className="mb-2 flex items-center gap-2 text-warn">
        <CreditCard size={14} />
        {isReal
          ? 'Razorpay Test Mode checkout — use test card 4111 1111 1111 1111'
          : 'Sandbox payment rail — deterministic mock authorization'}
      </p>
      <p className="mb-3 text-[11px] text-dim">
        order <span className="text-cyan">{initiation.order_id}</span> · amount{' '}
        <span className="text-emerald">{formatMinor(initiation.amount_minor, initiation.currency)}</span>
      </p>
      <button
        onClick={isReal ? payWithRazorpay : completeMock}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded border border-emerald/40 bg-emerald/10 px-3 py-1.5 text-[10px] font-semibold tracking-[0.18em] text-emerald uppercase hover:bg-emerald/20 disabled:opacity-50"
      >
        {busy && <Loader2 size={12} className="animate-spin" />}
        <Bot size={12} /> authorize test payment
      </button>
    </div>
  )
}

// Session id is stashed by run(); simplest reliable transport for the callback.
let LAST_SESSION_ID = ''
export function rememberSession(id: string): void {
  LAST_SESSION_ID = id
}
function currentSessionId(): string {
  return LAST_SESSION_ID
}

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void }
  }
}
interface RazorpayResponse {
  razorpay_order_id: string
  razorpay_payment_id: string
  razorpay_signature: string
}
interface RazorpayGlobal {
  Razorpay: new (options: Record<string, unknown>) => { open: () => void }
}

function loadRazorpayScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.Razorpay) return resolve()
    const s = document.createElement('script')
    s.src = 'https://checkout.razorpay.com/v1/checkout.js'
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('Failed to load Razorpay checkout'))
    document.head.appendChild(s)
  })
}
