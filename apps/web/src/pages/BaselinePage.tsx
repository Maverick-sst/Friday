import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, FileSearch } from 'lucide-react'
import { team, streamMissionEvents, type MissionSummary } from '../lib/team'
import { Empty, Panel, SectionHead, Spinner, StatusChip } from '../components/kit'

interface FeedLine {
  kind: string
  text: string
  ts: number
}

export function BaselinePage({
  merchantId,
  onOpenMission,
  onDone,
}: {
  merchantId: string
  onOpenMission: (missionId: string) => void
  onDone: () => void
}) {
  const [missions, setMissions] = useState<MissionSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [feed, setFeed] = useState<FeedLine[]>([])
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const feedRef = useRef<HTMLDivElement>(null)

  const pickBaseline = useCallback(
    (rows: MissionSummary[]) => {
      const baseline = rows.find((m) => m.mission_type === 'baseline')
      return baseline?.id ?? rows[0]?.id ?? null
    },
    [],
  )

  useEffect(() => {
    let alive = true
    team
      .listMissions(merchantId)
      .then((rows) => {
        if (!alive) return
        setMissions(rows)
        setActiveId((cur) => cur ?? pickBaseline(rows))
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [merchantId, pickBaseline])

  useEffect(() => {
    if (!activeId) return
    setFeed([])
    setSummary(null)
    let done = false
    const dispose = streamMissionEvents(activeId, (ev) => {
      const line: FeedLine = {
        kind: String(ev.kind),
        text:
          ev.kind === 'run_status'
            ? `${ev.agent} → ${String(ev.status)}${ev.objective ? `: ${String(ev.objective).slice(0, 90)}` : ''}`
            : ev.kind === 'tool_call'
              ? `${ev.agent} · ${ev.capability} ${ev.target ?? ''} — ${ev.status}`
              : ev.kind === 'mission_status'
                ? `mission ${ev.status}`
                : String(ev.label ?? ''),
        ts: Number(ev.ts) * 1000 || Date.now(),
      }
      if (line.text) setFeed((prev) => [...prev.slice(-200), line])
      if (ev.kind === 'mission_status') {
        setStatus(String(ev.status))
        if (['COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'TIMED_OUT', 'CANCELLED'].includes(String(ev.status))) {
          done = true
          team
            .getMission(activeId)
            .then((d) => setSummary(d.result_summary_json))
            .catch(() => {})
        }
      }
    })
    // Also fetch current state in case the mission already finished.
    team.getMission(activeId).then((d) => {
      setStatus(d.status)
      if (['COMPLETED', 'PARTIALLY_COMPLETED'].includes(d.status)) {
        done = true
        setSummary(d.result_summary_json)
      }
    }).catch(() => {})
    return () => {
      dispose()
      void done
    }
  }, [activeId])

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight })
  }, [feed])

  const terminal = status && ['COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'TIMED_OUT', 'CANCELLED'].includes(status)

  return (
    <div className="fade-up space-y-8">
      <SectionHead
        eyebrow="day-0 diagnostic"
        title="Your team is studying your business."
        support="Market research, competitor discovery, reputation scan and buyer simulations run in parallel. Strategy synthesizes everything into a versioned baseline."
        right={
          terminal ? (
            <button onClick={onDone} className="btn-primary">
              Meet your team <ArrowRight size={14} />
            </button>
          ) : (
            <span className="flex items-center gap-2 font-mono text-[11px] text-brand">
              <Spinner /> working…
            </span>
          )
        }
      />

      {missions.length === 0 && (
        <Empty>No missions yet — onboard a store to trigger the baseline.</Empty>
      )}

      {missions.length > 0 && (
        <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
          {/* Live activity */}
          <Panel pad={false} className="overflow-hidden">
            <header className="flex items-center justify-between border-b border-edge px-4 py-2.5">
              <span className="eyebrow">live agent activity</span>
              {status && <StatusChip status={status} />}
            </header>
            <div ref={feedRef} className="max-h-[440px] min-h-[320px] overflow-y-auto p-3">
              {feed.length === 0 && (
                <p className="mono-data p-3">waiting for events…</p>
              )}
              <ul className="space-y-1">
                {feed.map((line, i) => (
                  <li key={i} className="fade-up flex items-start gap-2 rounded px-2 py-1 hover:bg-white/[0.03]">
                    <span className="mt-0.5 shrink-0">
                      {line.kind === 'tool_call' ? (
                        <FileSearch size={11} className="text-dim" />
                      ) : (
                        <span className={`mt-1 block h-1.5 w-1.5 rounded-full ${line.kind === 'mission_status' ? 'bg-brand' : 'bg-dim'}`} />
                      )}
                    </span>
                    <span className={`font-mono text-[11px] leading-relaxed ${line.kind === 'mission_status' ? 'text-brand' : 'text-mute'}`}>
                      {line.text}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </Panel>

          {/* Missions + result summary */}
          <div className="space-y-5">
            <Panel>
              <span className="eyebrow">missions</span>
              <ul className="mt-3 space-y-1.5">
                {missions.map((m) => (
                  <li key={m.id}>
                    <button
                      onClick={() => onOpenMission(m.id)}
                      className={`flex w-full items-center gap-2 rounded-md border px-3 py-2 text-left transition-colors ${
                        m.id === activeId ? 'border-brand/40 bg-brand/[0.06]' : 'border-edge hover:border-edge-strong'
                      }`}
                    >
                      <StatusChip status={m.status} />
                      <span className="truncate text-[12.5px] text-ink">{m.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </Panel>

            {summary && (
              <Panel className="fade-up">
                <span className="eyebrow">baseline result</span>
                <dl className="mono-data mt-3 space-y-1.5">
                  {Object.entries(summary)
                    .filter(([k]) => !k.startsWith('_'))
                    .slice(0, 8)
                    .map(([k, v]) => (
                      <div key={k} className="flex items-baseline justify-between gap-3">
                        <dt className="shrink-0">{k.replaceAll('_', ' ')}</dt>
                        <dd className="truncate text-right text-ink">{String(v).slice(0, 80)}</dd>
                      </div>
                    ))}
                </dl>
              </Panel>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
