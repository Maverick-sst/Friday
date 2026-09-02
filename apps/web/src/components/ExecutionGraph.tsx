/** Execution graph (Fleet PRD A3/A4): mission → depth-0 agents → spawned
 * children as interconnected nodes on a clean org-chart canvas.
 *
 * Pure-CSS connectors (pseudo-element rails/stubs) — no measuring, no graph
 * library, no layout jitter. Nodes carry live state from the SSE stream
 * (current tool, tool budget, status ring) and click through to the run
 * inspector panel. Children attach to their parent via parent_run_id.
 */

interface GraphRun {
  id: string
  agent_key: string
  depth: number
  status: string
  tool_calls_used: number
  budget_tool_calls?: number
  latency_ms?: number | null
  parent_run_id?: string | null
}

interface GraphLive {
  status?: string
  currentTool?: string
  toolCalls?: number
}

const CSS = `
.exec-graph ul.exec-level {
  display: flex; justify-content: center; gap: 14px; margin: 0; padding: 0;
  position: relative; list-style: none;
}
.exec-graph li {
  display: flex; flex-direction: column; align-items: center;
  position: relative; padding: 30px 10px 0;
}
/* vertical stub from the sibling rail down to this node */
.exec-graph li::before {
  content: ''; position: absolute; top: 0; left: 50%;
  width: 1px; height: 30px; background: rgba(255,255,255,0.14);
}
/* horizontal rail joining siblings */
.exec-graph li::after {
  content: ''; position: absolute; top: 0; left: 0; right: 0;
  height: 1px; background: rgba(255,255,255,0.14);
}
.exec-graph li:first-child::after { left: 50%; }
.exec-graph li:last-child::after { right: 50%; }
.exec-graph li:only-child::after { display: none; }
/* vertical stub from a parent node down to its children's rail */
.exec-graph .exec-level::before {
  content: ''; position: absolute; top: -30px; left: 50%;
  width: 1px; height: 30px; background: rgba(255,255,255,0.14);
}
`

function statusRing(status: string): string {
  if (status === 'RUNNING') return 'border-brand/60 shadow-[0_0_16px_rgba(56,189,248,0.22)]'
  if (status === 'COMPLETED') return 'border-ok/40'
  if (status === 'PARTIALLY_COMPLETED') return 'border-warn/40'
  if (status === 'FAILED' || status === 'TIMED_OUT') return 'border-danger/50'
  return 'border-edge'
}

function statusDot(status: string): string {
  if (status === 'RUNNING') return 'bg-brand pulse-dot'
  if (status === 'COMPLETED') return 'bg-ok'
  if (status === 'PARTIALLY_COMPLETED') return 'bg-warn'
  if (status === 'FAILED' || status === 'TIMED_OUT') return 'bg-danger'
  return 'bg-dim'
}

export function ExecutionGraph({
  runs,
  agentLive = {},
  onOpenRun,
}: {
  runs: GraphRun[]
  agentLive?: Record<string, GraphLive>
  /** Called with the run's id; the page resolves the full run from its state. */
  onOpenRun?: (runId: string) => void
}) {
  if (runs.length === 0) {
    return (
      <div className="card flex items-center justify-center px-6 py-14 text-center text-[13px] text-dim">
        No agent runs yet.
      </div>
    )
  }

  const childrenOf = new Map<string, GraphRun[]>()
  const roots: GraphRun[] = []
  const ids = new Set(runs.map((r) => r.id))
  for (const r of runs) {
    if (r.parent_run_id && ids.has(r.parent_run_id)) {
      const list = childrenOf.get(r.parent_run_id) ?? []
      list.push(r)
      childrenOf.set(r.parent_run_id, list)
    } else {
      roots.push(r)
    }
  }

  const renderRun = (run: GraphRun) => {
    const live = agentLive[run.agent_key]
    const kids = childrenOf.get(run.id) ?? []
    const status = live?.status ?? run.status
    return (
      <li key={run.id}>
        <button
          onClick={() => onOpenRun?.(run.id)}
          title="inspect run"
          className={`exec-node w-44 rounded-lg border bg-[#111214] p-3 text-left transition-colors hover:border-edge-strong ${statusRing(status)}`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-[10.5px] uppercase tracking-wide text-brand">{run.agent_key}</span>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDot(status)}`} />
          </div>
          <p className="mt-1.5 min-h-[15px] truncate text-[11px] text-mute" title={live?.currentTool}>
            {live?.currentTool ?? (status === 'RUNNING' ? 'working…' : '—')}
          </p>
          <div className="mono-data mt-1.5 flex items-center justify-between">
            <span>
              {live?.toolCalls ?? run.tool_calls_used}
              {run.budget_tool_calls ? `/${run.budget_tool_calls}` : ''} tools
            </span>
            {run.depth > 0 && <span className="text-dim">d{run.depth}</span>}
          </div>
        </button>
        {kids.length > 0 && <ul className="exec-level">{kids.map(renderRun)}</ul>}
      </li>
    )
  }

  return (
    <div className="exec-graph overflow-x-auto pb-2">
      <style>{CSS}</style>
      <div className="mx-auto flex w-max flex-col items-center px-4 pt-1">
        {/* mission root */}
        <div className="rounded-lg border border-edge-strong bg-white/[0.04] px-5 py-2 text-center">
          <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-mute">mission</div>
          <div className="mono-data mt-0.5">{runs.length} agent runs</div>
        </div>
        <ul className="exec-level">{roots.map(renderRun)}</ul>
      </div>
    </div>
  )
}