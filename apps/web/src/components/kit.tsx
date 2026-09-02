/**
 * Design-system kit for the strategy-team UI.
 * Palette/type roles borrowed from aside.com's public language
 * (near-black surfaces #0a0a0a/#171717, sky accent, white/10 borders,
 * eyebrow -> headline -> one-line proof rhythm); structure is ours.
 */

import type { ReactNode } from 'react'

export function Eyebrow({ children }: { children: ReactNode }) {
  return <span className="eyebrow">{children}</span>
}

export function SectionHead({
  eyebrow,
  title,
  support,
  right,
}: {
  eyebrow: string
  title: string
  support?: string
  right?: ReactNode
}) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div>
        <Eyebrow>{eyebrow}</Eyebrow>
        <h2 className="h2 mt-2 text-ink">{title}</h2>
        {support && <p className="mt-1.5 max-w-xl text-[13px] text-mute">{support}</p>}
      </div>
      {right && <div className="shrink-0 pb-1">{right}</div>}
    </div>
  )
}

export function Panel({
  children,
  className = '',
  pad = true,
}: {
  children: ReactNode
  className?: string
  pad?: boolean
}) {
  return <section className={`card ${pad ? 'p-5' : ''} ${className}`}>{children}</section>
}

const STATUS_STYLES: Record<string, string> = {
  COMPLETED: 'bg-ok/10 text-ok border-ok/30',
  PARTIALLY_COMPLETED: 'bg-warn/10 text-warn border-warn/30',
  RUNNING: 'bg-brand/10 text-brand border-brand/40',
  QUEUED: 'bg-mute/10 text-mute border-edge-strong',
  CREATED: 'bg-mute/10 text-mute border-edge-strong',
  PENDING: 'bg-mute/10 text-mute border-edge-strong',
  FAILED: 'bg-danger/10 text-danger border-danger/30',
  TIMED_OUT: 'bg-danger/10 text-danger border-danger/30',
  CANCELLED: 'bg-dim/15 text-dim border-edge',
}

export function StatusChip({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? 'bg-mute/10 text-mute border-edge'
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] ${cls}`}
    >
      {status === 'RUNNING' && <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-current" />}
      {status.replaceAll('_', ' ').toLowerCase()}
    </span>
  )
}

export function SeverityChip({ severity }: { severity: string }) {
  const cls =
    severity === 'critical' || severity === 'high'
      ? 'bg-danger/10 text-danger border-danger/30'
      : severity === 'medium'
        ? 'bg-warn/10 text-warn border-warn/30'
        : 'bg-mute/10 text-mute border-edge'
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-px font-mono text-[10px] ${cls}`}>
      {severity}
    </span>
  )
}

/** Dev-tool diff badge in aside's "+6 -0" flavor — evidence counts. */
export function DiffBadge({ plus, minus = 0 }: { plus: number; minus?: number }) {
  return (
    <span className="font-mono text-[10px] tracking-tight">
      {plus > 0 && <span className="text-ok">+{plus}</span>}
      {minus > 0 && <span className="ml-1 text-danger">−{minus}</span>}
      {plus === 0 && minus === 0 && <span className="text-dim">±0</span>}
    </span>
  )
}

export function SimulatedTag({ small = false }: { small?: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border border-warn/40 bg-warn/10 font-mono uppercase tracking-[0.14em] text-warn ${
        small ? 'px-1.5 py-px text-[9px]' : 'px-2 py-0.5 text-[10px]'
      }`}
      title="Simulated counterfactual result — not real production revenue"
    >
      simulated
    </span>
  )
}

export function FactChip({ state }: { state: string }) {
  const map: Record<string, [string, string]> = {
    fact: ['fact', 'text-ok border-ok/30 bg-ok/10'],
    inference: ['inference', 'text-brand border-brand/30 bg-brand/10'],
    speculation: ['speculation', 'text-warn border-warn/30 bg-warn/10'],
  }
  const [label, cls] = map[state] ?? [state, 'text-mute border-edge']
  return (
    <span className={`rounded border px-1.5 py-px font-mono text-[9px] uppercase tracking-wide ${cls}`}>
      {label}
    </span>
  )
}

/** Benchmark-style horizontal bar (aside §6.6 pattern). */
export function StatBar({
  label,
  value,
  max = 100,
  accent = false,
  suffix = '',
}: {
  label: string
  value: number
  max?: number
  accent?: boolean
  suffix?: string
}) {
  const pct = Math.max(2, Math.min(100, (value / max) * 100))
  return (
    <div className="flex items-center gap-3">
      <span className={`w-44 shrink-0 truncate text-[12px] ${accent ? 'text-ink' : 'text-mute'}`}>{label}</span>
      <div className="h-4 flex-1 overflow-hidden rounded-sm bg-white/[0.05]">
        <div
          className={`h-full rounded-sm transition-all duration-500 ${
            accent ? 'bg-brand' : 'bg-white/25'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`w-16 shrink-0 text-right font-mono text-[11px] ${accent ? 'text-brand' : 'text-mute'}`}>
        {value}
        {suffix}
      </span>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="card flex items-center justify-center px-6 py-14 text-center text-[13px] text-dim">
      {children}
    </div>
  )
}

export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className="animate-spin text-brand">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" opacity="0.2" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  )
}

export function Confidence({ value }: { value: number | null | undefined }) {
  if (value == null) return null
  const pct = Math.round(value * 100)
  return (
    <span className="font-mono text-[10px] text-mute" title="confidence">
      conf {pct}%
    </span>
  )
}

/** Right-hand inspector drawer (Fleet PRD A4): drill into findings/runs. */
export function SidePanel({
  open,
  title,
  eyebrow,
  onClose,
  children,
}: {
  open: boolean
  title: string
  eyebrow?: string
  onClose: () => void
  children: ReactNode
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-md flex-col border-l border-edge bg-bg shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-edge px-5 py-4">
          <div className="min-w-0">
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            <h3 className="display mt-1 truncate text-[14px] font-semibold text-ink">{title}</h3>
          </div>
          <button onClick={onClose} className="btn-ghost shrink-0" aria-label="Close panel">
            ✕
          </button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">{children}</div>
      </aside>
    </div>
  )
}

/** One cited source row inside the inspector (Fleet PRD A4). */
export function EvidenceRow({
  claim,
  sourceUrl,
  sourceType,
  excerpt,
  epistemicState,
  observedAt,
}: {
  claim: string
  sourceUrl: string | null
  sourceType?: string
  excerpt?: string | null
  epistemicState?: string
  observedAt?: string | null
}) {
  return (
    <div className="card p-3">
      <div className="flex flex-wrap items-center gap-2">
        {epistemicState && <FactChip state={epistemicState} />}
        {sourceType && <span className="mono-data">{sourceType}</span>}
        {observedAt && (
          <span className="mono-data ml-auto">{new Date(observedAt).toLocaleTimeString()}</span>
        )}
      </div>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink">{claim}</p>
      {excerpt && <p className="mt-1 line-clamp-3 text-[11.5px] italic leading-relaxed text-mute">“{excerpt}”</p>}
      {sourceUrl && (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-1.5 inline-block max-w-full truncate font-mono text-[10.5px] text-brand hover:underline"
        >
          {sourceUrl} ↗
        </a>
      )}
    </div>
  )
}
