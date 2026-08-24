import { useState } from 'react'
import {
  Activity,
  Bot,
  Boxes,
  Plug,
  ScrollText,
  SlidersHorizontal,
} from 'lucide-react'
import { AsciiBanner } from './components/ui'

export type PageId = 'connect' | 'profile' | 'policies' | 'console' | 'trace'

const NAV: { id: PageId; label: string; icon: React.ReactNode }[] = [
  { id: 'connect', label: 'Connect Store', icon: <Plug size={13} /> },
  { id: 'profile', label: 'AI-Native Profile', icon: <Boxes size={13} /> },
  { id: 'policies', label: 'Policies', icon: <SlidersHorizontal size={13} /> },
  { id: 'console', label: 'Agent Console', icon: <Bot size={13} /> },
  { id: 'trace', label: 'Transaction Trace', icon: <ScrollText size={13} /> },
]

export function Shell({
  page,
  onNavigate,
  children,
}: {
  page: PageId
  onNavigate: (p: PageId) => void
  children: React.ReactNode
}) {
  const [live] = useState(true)

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-edge bg-panel/60 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-5 py-3">
          <div className="flex items-center gap-2 text-emerald">
            <Activity size={16} strokeWidth={1.75} />
            <span className="font-[family-name:var(--font-display)] text-sm font-semibold tracking-[0.22em] uppercase">
              Agent&nbsp;Commerce
            </span>
          </div>
          <div className="hidden xl:block overflow-hidden max-w-xl opacity-80">
            <AsciiBanner />
          </div>
          <div className="ml-auto flex items-center gap-1.5 text-[10px] tracking-[0.15em] text-mute uppercase">
            <span className={`h-1.5 w-1.5 rounded-full ${live ? 'bg-emerald pulse-dot' : 'bg-danger'}`} />
            {live ? 'gateway online' : 'offline'}
          </div>
        </div>
        <nav className="mx-auto flex max-w-6xl gap-1 px-5">
          {NAV.map(({ id, label, icon }) => (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              className={`flex items-center gap-1.5 rounded-t border-x border-t px-3 py-2 text-[11px] tracking-wide transition-colors ${
                page === id
                  ? 'border-edge-bright bg-panel text-emerald'
                  : 'border-transparent text-mute hover:text-ink hover:bg-panel/50'
              }`}
            >
              {icon}
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-6">{children}</main>

      <footer className="border-t border-edge bg-panel/40">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-3 text-[10px] tracking-[0.14em] text-dim uppercase">
          <span>Shopify V0 · Razorpay Test Mode · Deterministic Policy Engine</span>
          <span className="font-mono">the llm proposes — the gateway authorizes</span>
        </div>
      </footer>
    </div>
  )
}
