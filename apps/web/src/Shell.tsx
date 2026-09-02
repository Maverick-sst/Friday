import { useEffect, useState } from 'react'
import { Activity, ChevronDown, History, Radar } from 'lucide-react'
import { team } from './lib/team'

export type PageId =
  | 'onboarding'
  | 'baseline'
  | 'team'
  | 'mission'
  | 'strategy'
  | 'legacy-connect'
  | 'legacy-profile'
  | 'legacy-policies'
  | 'legacy-console'
  | 'legacy-trace'

const MAIN_NAV: { id: PageId; label: string }[] = [
  { id: 'onboarding', label: 'Onboard' },
  { id: 'baseline', label: 'Baseline' },
  { id: 'team', label: 'AI Team' },
  { id: 'strategy', label: 'Strategy' },
]

const LEGACY_NAV: { id: PageId; label: string }[] = [
  { id: 'legacy-connect', label: 'Connect Store' },
  { id: 'legacy-profile', label: 'AI-Native Profile' },
  { id: 'legacy-policies', label: 'Policies' },
  { id: 'legacy-console', label: 'Agent Console' },
  { id: 'legacy-trace', label: 'Transaction Trace' },
]

function MetaChip() {
  const [meta, setMeta] = useState<Awaited<ReturnType<typeof team.meta>> | null>(null)
  useEffect(() => {
    let alive = true
    team
      .meta()
      .then((m) => alive && setMeta(m))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])
  if (!meta) return null
  const ok = meta.llm_configured && meta.composio_ready && meta.mem0_ready
  return (
    <span
      className="hidden items-center gap-1.5 rounded-md border border-edge px-2 py-1 font-mono text-[10px] text-mute md:inline-flex"
      title={`queue=${meta.queue_driver} llm=${meta.llm_configured} composio=${meta.composio_ready} mem0=${meta.mem0_ready}`}
    >
      <Activity size={11} className={ok ? 'text-ok' : 'text-warn'} />
      {ok ? 'fleet online' : 'degraded'}
    </span>
  )
}

export function Shell({
  page,
  onNavigate,
  children,
}: {
  page: PageId
  onNavigate: (p: PageId) => void
  children: React.ReactNode
}) {
  const [legacyOpen, setLegacyOpen] = useState(false)
  const isLegacy = page.startsWith('legacy')

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-40 border-b border-edge bg-bg/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-5">
          <button onClick={() => onNavigate('onboarding')} className="flex items-center gap-2" aria-label="AI Commerce home">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-brand font-display text-[13px] font-bold text-[#04212e]">
              A
            </span>
            <span className="display text-[13px] font-semibold tracking-tight text-ink">
              AI Commerce <span className="text-mute">Strategy Team</span>
            </span>
          </button>

          <nav className="ml-4 hidden items-center gap-0.5 md:flex">
            {MAIN_NAV.map(({ id, label }) => (
              <button
                key={id}
                onClick={() => onNavigate(id)}
                className={`rounded-md px-3 py-1.5 text-[12.5px] transition-colors ${
                  page === id || (id === 'team' && page === 'mission')
                    ? 'bg-white/[0.07] text-ink'
                    : 'text-mute hover:text-ink'
                }`}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <MetaChip />
            <div className="relative">
              <button
                onClick={() => setLegacyOpen((v) => !v)}
                className={`flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-[12px] transition-colors ${
                  isLegacy ? 'border-brand/40 text-brand' : 'border-edge text-mute hover:text-ink'
                }`}
              >
                <History size={12} />
                V0 Commerce
                <ChevronDown size={12} className={`transition-transform ${legacyOpen ? 'rotate-180' : ''}`} />
              </button>
              {legacyOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setLegacyOpen(false)} />
                  <div className="card absolute right-0 top-full z-50 mt-1.5 w-52 p-1 fade-up">
                    {LEGACY_NAV.map(({ id, label }) => (
                      <button
                        key={id}
                        onClick={() => {
                          setLegacyOpen(false)
                          onNavigate(id)
                        }}
                        className="block w-full rounded-md px-3 py-2 text-left text-[12.5px] text-mute hover:bg-white/[0.06] hover:text-ink"
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {/* mobile nav */}
        <nav className="flex gap-0.5 overflow-x-auto border-t border-edge px-3 py-1.5 md:hidden">
          {MAIN_NAV.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              className={`whitespace-nowrap rounded-md px-2.5 py-1 text-[12px] ${
                page === id ? 'bg-white/[0.07] text-ink' : 'text-mute'
              }`}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-8">{children}</main>

      {/* Final CTA band — aside §6.13 pattern, our copy */}
      <footer className="mt-10 border-t border-edge">
        <div className="mx-auto max-w-6xl px-5 py-8">
          <div className="card flex flex-col items-center gap-4 px-6 py-8 text-center sm:flex-row sm:justify-between sm:text-left">
            <div>
              <div className="display text-[15px] font-semibold text-ink">
                Your market never sleeps. Neither does your team.
              </div>
              <p className="mt-0.5 text-[12.5px] text-mute">
                Launch a mission and let the fleet gather evidence.
              </p>
            </div>
            <button onClick={() => onNavigate('team')} className="btn-primary shrink-0">
              <Radar size={14} />
              Launch a mission
            </button>
          </div>
          <p className="mono-data mt-5 flex items-center justify-center gap-1.5">
            <Radar size={10} className="text-brand" /> persistent intelligence · evidence before conclusions ·
            simulated metrics are always labeled
          </p>
        </div>
      </footer>
    </div>
  )
}
