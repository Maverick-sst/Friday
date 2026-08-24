import { useEffect, useState } from 'react'
import { Save, ShieldAlert } from 'lucide-react'
import { Panel } from '../components/ui'
import { api } from '../lib/api'
import { formatMinor } from '../lib/money'
import type { PoliciesPayload } from '../lib/types'

const CATEGORY_PRESETS = ['running_shoes', 'sportswear', 'apparel', 'accessories', 'electronics']

export function PoliciesPage({ merchantId }: { merchantId: string }) {
  const [policies, setPolicies] = useState<PoliciesPayload | null>(null)
  const [maxMajor, setMaxMajor] = useState('5000')
  const [approvalMajor, setApprovalMajor] = useState('5000')
  const [categories, setCategories] = useState<string[]>([])
  const [returnDays, setReturnDays] = useState(7)
  const [allowCancel, setAllowCancel] = useState(true)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<PoliciesPayload>(`/api/v1/merchants/${merchantId}/policies`)
      .then((p) => {
        setPolicies(p)
        setMaxMajor(String(p.max_auto_purchase_minor / 100))
        setApprovalMajor(String(p.approval_threshold_minor / 100))
        setCategories(p.allowed_categories)
        setReturnDays(p.return_window_days)
        setAllowCancel(p.allow_cancellation)
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [merchantId])

  const toggleCategory = (c: string) =>
    setCategories((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await api.put<PoliciesPayload>(`/api/v1/merchants/${merchantId}/policies`, {
        max_auto_purchase_minor: Math.round(Number(maxMajor) * 100),
        approval_threshold_minor: Math.round(Number(approvalMajor) * 100),
        allowed_categories: categories,
        return_window_days: returnDays,
        allow_cancellation: allowCancel,
      })
      setPolicies(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 1500)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (error && !policies) return <Panel title="Policies" tone="danger">{error}</Panel>
  if (!policies) return <Panel title="Policies"><p className="text-dim">loading policies…</p></Panel>

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
      <Panel title="Transaction policies" right={saved ? <span className="text-[10px] tracking-widest text-emerald uppercase">saved ✓</span> : undefined}>
        <div className="space-y-5">
          <NumberField
            label="maximum automatic purchase"
            hint={`agents may complete purchases up to ${formatMinor(Math.round(Number(maxMajor) * 100))} without human approval`}
            value={maxMajor}
            onChange={setMaxMajor}
            suffix="INR"
          />
          <NumberField
            label="human approval required above"
            hint="transactions above this threshold are escalated to a human"
            value={approvalMajor}
            onChange={setApprovalMajor}
            suffix="INR"
          />

          <div>
            <label className="mb-1.5 block text-[10px] tracking-[0.16em] text-dim uppercase">
              allowed categories
            </label>
            <div className="flex flex-wrap gap-2">
              {CATEGORY_PRESETS.map((c) => (
                <button
                  key={c}
                  onClick={() => toggleCategory(c)}
                  className={`rounded border px-2.5 py-1 text-[11px] transition-colors ${
                    categories.includes(c)
                      ? 'border-emerald/50 bg-emerald/10 text-emerald'
                      : 'border-edge bg-panel-2 text-dim hover:text-mute'
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>
            {categories.length === 0 && (
              <p className="mt-2 text-[11px] text-warn">No categories selected — agents cannot buy anything.</p>
            )}
          </div>

          <div className="flex items-end gap-6">
            <NumberField label="return window" value={String(returnDays)} onChange={(v) => setReturnDays(Number(v) || 0)} suffix="days" />
            <div className="pb-0.5">
              <label className="mb-1.5 block text-[10px] tracking-[0.16em] text-dim uppercase">cancellation</label>
              <button
                onClick={() => setAllowCancel(!allowCancel)}
                className={`rounded border px-3 py-1.5 text-[11px] ${
                  allowCancel ? 'border-emerald/40 bg-emerald/10 text-emerald' : 'border-danger/40 bg-danger/10 text-danger'
                }`}
              >
                {allowCancel ? 'ALLOWED' : 'NOT ALLOWED'}
              </button>
            </div>
          </div>

          {error && <p className="text-[11px] text-danger">{error}</p>}

          <button
            onClick={save}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded border border-emerald/40 bg-emerald/10 px-4 py-2 text-[11px] font-semibold tracking-[0.18em] text-emerald uppercase hover:bg-emerald/20 disabled:opacity-50"
          >
            <Save size={13} /> Commit policy v{policies.version + (saved ? 0 : 1)}
          </button>
        </div>
      </Panel>

      <Panel title="How enforcement works" tone="success">
        <ol className="space-y-3 text-[12px] text-mute">
          <li><span className="text-cyan">01</span> — Buyer agent requests checkout with quote + cart</li>
          <li><span className="text-cyan">02</span> — Gateway revalidates live price &amp; inventory at the source platform</li>
          <li><span className="text-cyan">03</span> — Deterministic engine evaluates all twelve rules; the LLM has no vote here</li>
          <li><span className="text-cyan">04</span> — Effective limit = min(merchant cap, buyer authorization)</li>
          <li><span className="text-cyan">05</span> — Only an AUTHORIZED decision can create a payment order</li>
          <li><span className="text-cyan">06</span> — Every decision — pass or block — lands in the audit trail</li>
        </ol>
        <div className="mt-4 flex items-start gap-2 rounded border border-warn/25 bg-warn/5 p-3 text-[11px] text-warn">
          <ShieldAlert size={14} className="mt-0.5 shrink-0" />
          Policy edits take effect immediately for new transactions.
        </div>
      </Panel>
    </div>
  )
}

function NumberField({
  label,
  hint,
  value,
  onChange,
  suffix,
}: {
  label: string
  hint?: string
  value: string
  onChange: (v: string) => void
  suffix?: string
}) {
  return (
    <div>
      <label className="mb-1.5 block text-[10px] tracking-[0.16em] text-dim uppercase">{label}</label>
      <div className="flex w-full max-w-xs items-center gap-2 rounded border border-edge bg-[#070b11] px-3 focus-within:border-emerald/50">
        <input
          type="number"
          min={0}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-transparent py-2 font-mono text-[13px] outline-none"
        />
        {suffix && <span className="text-[11px] text-dim">{suffix}</span>}
      </div>
      {hint && <p className="mt-1 text-[11px] text-dim">{hint}</p>}
    </div>
  )
}
