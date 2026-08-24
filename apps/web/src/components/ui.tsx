import { useEffect, useRef, useState } from 'react'
import { Terminal } from 'lucide-react'

export function Panel({
  title,
  right,
  children,
  className = '',
  tone = 'default',
}: {
  title: string
  right?: React.ReactNode
  children: React.ReactNode
  className?: string
  tone?: 'default' | 'danger' | 'success'
}) {
  const toneColor =
    tone === 'danger' ? 'text-danger' : tone === 'success' ? 'text-emerald' : 'text-cyan'
  return (
    <section
      className={`rounded-md border border-edge bg-panel/80 backdrop-blur-sm shadow-[0_0_40px_-18px_rgba(34,211,238,0.25)] ${className}`}
    >
      <header className="flex items-center gap-2 border-b border-edge px-4 py-2.5">
        <span className={`flex items-center gap-1.5 text-[11px] tracking-[0.18em] uppercase ${toneColor}`}>
          <Terminal size={12} strokeWidth={1.75} />
          {title}
        </span>
        <div className="ml-auto flex items-center gap-2">{right}</div>
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    active: 'bg-emerald/10 text-emerald border-emerald/30',
    connected: 'bg-emerald/10 text-emerald border-emerald/30',
    COMPLETED: 'bg-emerald/10 text-emerald border-emerald/30',
    AUTHORIZED: 'bg-cyan/10 text-cyan border-cyan/30',
    PAYMENT_PENDING: 'bg-warn/10 text-warn border-warn/30',
    PAYMENT_SUCCESS: 'bg-emerald/10 text-emerald border-emerald/30',
    BLOCKED: 'bg-danger/10 text-danger border-danger/30',
    PAYMENT_FAILED: 'bg-danger/10 text-danger border-danger/30',
    QUOTE_CREATED: 'bg-cyan/10 text-cyan border-cyan/30',
    pending: 'bg-warn/10 text-warn border-warn/30',
  }
  const cls = map[status] ?? 'bg-mute/10 text-mute border-mute/30'
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[10px] font-medium tracking-[0.14em] uppercase ${cls}`}>
      {status.replaceAll('_', ' ')}
    </span>
  )
}

const BANNER = String.raw`
   ___   _   _____   _   _ ____      _    ____ _____ ____
  / _ \ / \ | ____| \ \ / | ___|    / \  / ___| ____|  _ \
 | | | / _ \|  _|    \ V /|  _|    / _ \| |  _|  _| | |_) |
 | |_| / ___ \ |___   | | | |___  / ___ \ |_| | |___|  _ <
  \___/_/   \_____|  |_| |_____|/_/   \_\____|_____|_| \_\
        A G E N T   C O M M E R C E   G A T E W A Y`

export function AsciiBanner() {
  return (
    <pre className="hidden select-none lg:block text-[9px] leading-[1.15] text-emerald/70 whitespace-pre">
      {BANNER}
    </pre>
  )
}

export function JsonViewer({ data, maxHeight = '24rem' }: { data: unknown; maxHeight?: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const json = JSON.stringify(data, null, 2)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const copy = async () => {
    await navigator.clipboard.writeText(json)
    setCopied(true)
    timer.current = window.setTimeout(() => setCopied(false), 1200)
  }

  return (
    <div className="relative group">
      <button
        onClick={copy}
        className="absolute right-2 top-2 z-10 rounded border border-edge bg-panel px-2 py-1 text-[10px] text-mute opacity-0 transition-opacity group-hover:opacity-100 hover:text-ink hover:border-edge-bright"
      >
        {copied ? 'COPIED ✓' : 'COPY'}
      </button>
      <pre
        className="overflow-auto rounded border border-edge bg-[#070b11] p-3 text-[11px] leading-relaxed whitespace-pre"
        style={{ maxHeight }}
      >
        {highlightJson(json)}
      </pre>
    </div>
  )
}

function highlightJson(json: string): React.ReactNode[] {
  // Lightweight tokenized highlighting - no external dependency.
  const parts = json.split(/("(?:\\.|[^"\\])*"(?:\s*:)?|\b\d+(?:\.\d+)?\b|\btrue\b|\bfalse\b|\bnull\b)/g)
  return parts.map((part, i) => {
    if (!part) return null
    if (part.startsWith('"') && part.endsWith(':')) {
      return (
        <span key={i}>
          <span className="text-cyan">{part.slice(0, -1)}</span>
          <span className="text-dim">:</span>
        </span>
      )
    }
    if (part.startsWith('"')) return <span key={i} className="text-emerald">{part}</span>
    if (/^-?\d/.test(part)) return <span key={i} className="text-warn">{part}</span>
    if (['true', 'false', 'null'].includes(part)) return <span key={i} className="text-danger">{part}</span>
    return <span key={i} className="text-mute">{part}</span>
  })
}
