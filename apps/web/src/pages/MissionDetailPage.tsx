import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Ban } from 'lucide-react'
import { team, streamMissionEvents, type MissionDetail } from '../lib/team'
import {
  Confidence,
  DiffBadge,
  Empty,
  EvidenceRow,
  Panel,
  SectionHead,
  SeverityChip,
  SidePanel,
  StatusChip,
} from '../components/kit'
import { ExecutionGraph } from '../components/ExecutionGraph'

interface RunRow {
  id: string
  agent_key: string
  depth: number
  status: string
  objective: string
  summary: string | null
  latency_ms: number | null
  tool_calls_used: number
  budget_tool_calls?: number
  parent_run_id?: string | null
  confidence: number | null
}

interface FindingRow {
  id: string
  title: string
  statement: string
  severity: string
  confidence: number | null
  evidence_ids_json: string[]
  evidence?: EvidenceBrief[]
}

interface EvidenceBrief {
  id: string
  claim: string
  source_url: string | null
  source_type?: string
  epistemic_state?: string
  excerpt?: string
  observed_at?: string | null
  agent_run_id?: string | null
}

interface RecommendationRow {
  id: string
  problem: string
  recommendation_text: string
  expected_impact: string | null
  why_it_matters: string | null
  confidence: number | null
  impact: string
  is_hypothesis: boolean
  priority_rank: number | null
  suggested_next_mission_json: { objective?: string }
}

interface MissionEvent {
  kind: string
  ts?: number
  trace_id?: string
  [k: string]: unknown
}

interface FeedItem {
  key: string
  time: string
  agent: string
  text: string
  traceId?: string
  link?: string
}

interface AgentLive {
  status?: string
  currentTool?: string
  toolCalls: number
}

const FEED_MAX = 60

function describeEvent(e: MissionEvent): { agent: string; text: string; link?: string } | null {
  const agent = String(e.agent ?? '')
  switch (e.kind) {
    case 'tool_call':
      return {
        agent,
        text: `${e.status === 'failed' ? 'tool failed' : 'tool'}: ${e.capability} → ${String(e.target ?? '').slice(0, 80)}${
          e.budget_used ? ` · budget ${e.budget_used}` : ''
        }`,
      }
    case 'run_status':
      return { agent, text: `run ${String(e.status ?? '').toLowerCase()}` }
    case 'agent.spawn.requested':
      return { agent, text: `spawning ${e.child_agent ?? 'scout'} → ${String(e.reason ?? '').slice(0, 90)}` }
    case 'agent.spawn.completed':
      return { agent, text: `scout spawn ${e.ok ? 'completed' : 'finished (rejected)'}` }
    case 'agent.spawn.rejected':
      return { agent, text: `scout spawn rejected: ${String(e.error ?? '')}` }
    case 'browser.session':
      return {
        agent,
        text: `browser session live — agent is browsing ${String(e.target ?? '').slice(0, 60)}`,
        link: typeof e.preview_url === 'string' && e.preview_url ? e.preview_url : undefined,
      }
    case 'mission_status':
      return { agent: 'mission', text: `mission ${String(e.status ?? '').toLowerCase()}` }
    case 'log':
      return { agent: agent || 'system', text: String(e.message ?? '').slice(0, 120) }
    default:
      return null
  }
}

export function MissionDetailPage({ missionId, onBack }: { missionId: string; onBack: () => void }) {
  const [mission, setMission] = useState<MissionDetail | null>(null)
  const [runs, setRuns] = useState<RunRow[]>([])
  const [findings, setFindings] = useState<FindingRow[]>([])
  const [recs, setRecs] = useState<RecommendationRow[]>([])
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [agentLive, setAgentLive] = useState<Record<string, AgentLive>>({})
  const [connState, setConnState] = useState<'connecting' | 'open' | 'closed'>('connecting')
  const [openFinding, setOpenFinding] = useState<FindingRow | null>(null)
  const [openRun, setOpenRun] = useState<RunRow | null>(null)
  const [runEvidence, setRunEvidence] = useState<EvidenceBrief[] | null>(null)
  const [langfuseUi, setLangfuseUi] = useState<string | null>(null)
  const seqRef = useRef(0)

  const refresh = useCallback(() => {
    team.getMission(missionId).then(setMission).catch(() => {})
    fetch(`/api/v1/team/missions/${missionId}/runs`).then((r) => r.json()).then(setRuns).catch(() => {})
    fetch(`/api/v1/team/missions/${missionId}/intel`)
      .then((r) => r.json())
      .then((d: { findings: FindingRow[]; recommendations: RecommendationRow[] }) => {
        setFindings(d.findings ?? [])
        setRecs(d.recommendations ?? [])
      })
      .catch(() => {})
  }, [missionId])

  useEffect(() => {
    team.meta().then((m) => setLangfuseUi(m.langfuse_ui ?? null)).catch(() => {})
  }, [])

  const handleEvent = useCallback(
    (e: MissionEvent) => {
      if (e.kind === 'snapshot') {
        const snap = e as unknown as { runs?: RunRow[] }
        if (snap.runs?.length) setRuns(snap.runs)
        return
      }
      // Live agent pipeline state (Fleet PRD A3).
      const agent = String(e.agent ?? '')
      if (agent && e.kind === 'tool_call') {
        setAgentLive((prev) => ({
          ...prev,
          [agent]: {
            ...prev[agent],
            currentTool: `${e.capability}: ${String(e.target ?? '').slice(0, 60)}`,
            toolCalls: (prev[agent]?.toolCalls ?? 0) + 1,
          },
        }))
      }
      if (agent && e.kind === 'run_status') {
        setAgentLive((prev) => ({ ...prev, [agent]: { ...prev[agent], status: String(e.status ?? '') } }))
        const runId = String(e.run_id ?? '')
        const status = String(e.status ?? '')
        if (runId) {
          setRuns((prev) =>
            prev.map((r) =>
              r.id === runId
                ? {
                    ...r,
                    status,
                    tool_calls_used: Number(e.tool_calls_used ?? r.tool_calls_used) || r.tool_calls_used,
                  }
                : r,
            ),
          )
        }
      }
      // Activity feed, newest first (Fleet PRD A3/A4).
      const desc = describeEvent(e)
      if (desc) {
        seqRef.current += 1
        setFeed((prev) =>
          [
            {
              key: `${e.ts ?? ''}-${seqRef.current}`,
              time: e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : '',
              agent: desc.agent,
              text: desc.text,
              traceId: e.trace_id,
              link: desc.link,
            },
            ...prev,
          ].slice(0, FEED_MAX),
        )
      }
      if (e.kind === 'mission_status' || e.kind === 'finding') refresh()
    },
    [refresh],
  )

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 8000)
    // Real SSE consumption (Fleet PRD A3): snapshot + gap-free replay + live.
    const dispose = streamMissionEvents(missionId, handleEvent, setConnState)
    return () => {
      clearInterval(t)
      dispose()
    }
  }, [missionId, refresh, handleEvent])

  const openRunPanel = (runId: string) => {
    const run = runs.find((r) => r.id === runId)
    if (!run) return
    setOpenRun(run)
    setRunEvidence(null)
    fetch(`/api/v1/team/missions/${missionId}/evidence`)
      .then((r) => r.json())
      .then((rows: EvidenceBrief[]) => setRunEvidence(rows.filter((x) => x.agent_run_id === run.id)))
      .catch(() => setRunEvidence([]))
  }

  if (!mission) return <p className="mono-data">loading mission…</p>

  const isActive = ['RUNNING', 'QUEUED'].includes(mission.status)
  const budgetPct = Math.min(100, Math.round((mission.runs_used / Math.max(mission.budget_runs, 1)) * 100))

  return (
    <div className="fade-up space-y-8">
      <button onClick={onBack} className="btn-ghost">
        <ArrowLeft size={13} /> back to team
      </button>

      {/* Header */}
      <section>
        <SectionHead
          eyebrow={`mission · ${mission.mission_type}`}
          title={mission.name}
          support={mission.objective}
          right={
            isActive ? (
              <button
                onClick={() => team.cancelMission(mission.id).then(refresh).catch(() => {})}
                className="btn-secondary"
              >
                <Ban size={13} /> Cancel
              </button>
            ) : (
              <StatusChip status={mission.status} />
            )
          }
        />
        <Panel>
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
            <div className="min-w-[180px] flex-1">
              <div className="mono-data mb-1 flex justify-between">
                <span>agent-run budget</span>
                <span>
                  {mission.runs_used}/{mission.budget_runs}
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-sm bg-white/[0.06]">
                <div
                  className={`h-full rounded-sm transition-all ${budgetPct >= 100 ? 'bg-warn' : 'bg-brand'}`}
                  style={{ width: `${budgetPct}%` }}
                />
              </div>
            </div>
            <div className="mono-data">
              tool calls <span className="text-ink">{mission.tool_calls_used}</span>
            </div>
            {mission.started_at && (
              <div className="mono-data">started {new Date(mission.started_at).toLocaleTimeString()}</div>
            )}
          </div>
        </Panel>
      </section>

      {/* Execution graph (Fleet PRD A3/A4): mission → agents → spawned children */}
      <section>
        <SectionHead
          eyebrow="live"
          title="Execution graph"
          support="Mission → specialists → spawned scouts, connected by parentage. Nodes carry live status straight off the SSE stream — click one to inspect its run."
        />
        <ExecutionGraph runs={runs} agentLive={agentLive} onOpenRun={openRunPanel} />

      </section>

      {/* Agent runs */}
      <section>
        <SectionHead eyebrow="execution" title="Agent runs" support="Parent runs at depth 0; spawned sub-agents nest one level." />
        {runs.length === 0 ? (
          <Empty>No agent runs recorded yet.</Empty>
        ) : (
          <ul className="space-y-2">
            {runs.map((r) => (
              <li key={r.id} className="card px-4 py-3" style={{ marginLeft: r.depth > 0 ? `${r.depth * 20}px` : 0 }}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <StatusChip status={r.status} />
                  <span className="font-mono text-[11px] uppercase tracking-wide text-brand">{r.agent_key}</span>
                  {r.depth > 0 && <span className="rounded border border-edge px-1 font-mono text-[9px] text-dim">child</span>}
                  <Confidence value={r.confidence} />
                  <span className="ml-auto mono-data">
                    {r.tool_calls_used} tools{r.latency_ms != null && ` · ${(r.latency_ms / 1000).toFixed(1)}s`}
                  </span>
                </div>
                {r.summary && <p className="mt-1.5 line-clamp-2 text-[12.5px] text-mute">{r.summary}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Live activity feed (Fleet PRD A3/A4: trace-linked) */}
      <section>
        <SectionHead
          eyebrow="runtime"
          title="Activity"
          support="Tool calls, spawn decisions and status changes — each row links to its Langfuse trace when tracing is on."
          right={
            <span className="mono-data flex items-center gap-2">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  connState === 'open' ? 'bg-ok' : connState === 'connecting' ? 'bg-warn pulse-dot' : 'bg-dim'
                }`}
              />
              {connState === 'open' ? 'live' : connState === 'connecting' ? 'connecting…' : 'stream closed'}
            </span>
          }
        />
        {feed.length === 0 ? (
          <Empty>No live activity yet — the stream replays everything you missed on connect.</Empty>
        ) : (
          <ul className="space-y-1.5">
            {feed.map((f) => (
              <li key={f.key} className="card flex items-center gap-3 px-3.5 py-2">
                <span className="mono-data w-16 shrink-0 text-dim">{f.time}</span>
                <span className="w-24 shrink-0 truncate font-mono text-[10.5px] uppercase text-brand">{f.agent}</span>
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-mute">{f.text}</span>
                {f.traceId && langfuseUi && (
                  <a
                    href={`${langfuseUi}/trace/${f.traceId}`}
                    target="_blank"
                    rel="noreferrer"
                    className="mono-data shrink-0 text-brand hover:underline"
                    title={`trace ${f.traceId}`}
                  >
                    trace ↗
                  </a>
                )}
                {f.link && (
                  <a
                    href={f.link}
                    target="_blank"
                    rel="noreferrer"
                    className="mono-data shrink-0 text-brand hover:underline"
                    title="watch the live browser session"
                  >
                    watch ↗
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Findings */}
      <section>
        <SectionHead
          eyebrow="what the team found"
          title="Findings"
          support="Click a finding to inspect every source behind it."
        />
        {findings.length === 0 ? (
          <Empty>No findings yet.</Empty>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">
            {findings.map((f) => (
              <li key={f.id}>
                <button
                  onClick={() => setOpenFinding(f)}
                  className="card w-full p-4 text-left transition-colors hover:border-edge-strong"
                >
                  <div className="flex items-center gap-2">
                    <SeverityChip severity={f.severity} />
                    <DiffBadge plus={f.evidence_ids_json.length} minus={0} />
                    <span className="mono-data ml-auto">evidence</span>
                  </div>
                  <h4 className="display mt-2 text-[13.5px] font-semibold text-ink">{f.title}</h4>
                  <p className="mt-1 line-clamp-3 text-[12.5px] leading-relaxed text-mute">{f.statement}</p>
                  <div className="mt-2">
                    <Confidence value={f.confidence} />
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Recommendations */}
      {recs.length > 0 && (
        <section>
          <SectionHead eyebrow="closing synthesis" title="Recommendations" />
          <ol className="space-y-3">
            {[...recs]
              .sort((a, b) => (a.priority_rank ?? 99) - (b.priority_rank ?? 99))
              .map((r) => (
                <li key={r.id} className="card p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[10px] text-dim">#{r.priority_rank ?? '—'}</span>
                    {r.is_hypothesis && (
                      <span className="rounded border border-warn/40 bg-warn/10 px-1.5 py-px font-mono text-[9px] uppercase text-warn">
                        hypothesis
                      </span>
                    )}
                    <Confidence value={r.confidence} />
                    <span className="ml-auto mono-data">impact {r.impact}</span>
                  </div>
                  <p className="mt-2 text-[13.5px] font-medium text-ink">{r.problem}</p>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-mute">{r.recommendation_text}</p>
                  {r.suggested_next_mission_json?.objective && (
                    <p className="mt-2 border-l-2 border-brand/50 pl-2.5 text-[12px] text-brand">
                      next mission: {r.suggested_next_mission_json.objective}
                    </p>
                  )}
                </li>
              ))}
          </ol>
        </section>
      )}

      {/* Finding inspector (Fleet PRD A4) */}
      <SidePanel
        open={openFinding !== null}
        title={openFinding?.title ?? ''}
        eyebrow="finding inspector"
        onClose={() => setOpenFinding(null)}
      >
        {openFinding && (
          <>
            <div className="flex items-center gap-2">
              <SeverityChip severity={openFinding.severity} />
              <Confidence value={openFinding.confidence} />
              <span className="mono-data ml-auto">{openFinding.evidence_ids_json.length} sources</span>
            </div>
            <p className="text-[13px] leading-relaxed text-ink">{openFinding.statement}</p>
            {(openFinding.evidence ?? []).length === 0 ? (
              <p className="mono-data text-dim">no resolved evidence briefs for this finding</p>
            ) : (
              (openFinding.evidence ?? []).map((ev) => (
                <EvidenceRow
                  key={ev.id}
                  claim={ev.claim}
                  sourceUrl={ev.source_url}
                  sourceType={ev.source_type}
                  excerpt={ev.excerpt}
                  epistemicState={ev.epistemic_state}
                  observedAt={ev.observed_at}
                />
              ))
            )}
          </>
        )}
      </SidePanel>

      {/* Run inspector (Fleet PRD A4): every tool call + source the run touched */}
      <SidePanel
        open={openRun !== null}
        title={`${openRun?.agent_key ?? ''} run`}
        eyebrow="run inspector"
        onClose={() => setOpenRun(null)}
      >
        {openRun && (
          <>
            <div className="flex items-center gap-2">
              <StatusChip status={openRun.status} />
              <Confidence value={openRun.confidence} />
              <span className="mono-data ml-auto">
                {openRun.tool_calls_used} tools
                {openRun.latency_ms != null && ` · ${(openRun.latency_ms / 1000).toFixed(1)}s`}
              </span>
            </div>
            <p className="text-[12.5px] leading-relaxed text-mute">{openRun.objective}</p>
            {openRun.summary && <p className="text-[12.5px] leading-relaxed text-ink">{openRun.summary}</p>}
            {runEvidence === null ? (
              <p className="mono-data text-dim">loading tool provenance…</p>
            ) : runEvidence.length === 0 ? (
              <p className="mono-data text-dim">no evidence recorded for this run</p>
            ) : (
              runEvidence.map((ev) => (
                <EvidenceRow
                  key={ev.id}
                  claim={ev.claim}
                  sourceUrl={ev.source_url}
                  sourceType={ev.source_type}
                  excerpt={ev.excerpt}
                  epistemicState={ev.epistemic_state}
                  observedAt={ev.observed_at}
                />
              ))
            )}
          </>
        )}
      </SidePanel>
    </div>
  )
}
