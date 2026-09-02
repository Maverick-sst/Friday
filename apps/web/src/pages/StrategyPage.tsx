import { useCallback, useEffect, useState } from 'react'
import { FlaskConical, Play } from 'lucide-react'
import { team, type ExperimentDetail } from '../lib/team'
import { Confidence, Empty, Panel, SectionHead, SimulatedTag, StatBar, StatusChip } from '../components/kit'

interface RecommendationRow {
  id: string
  problem: string
  recommendation_text: string
  expected_impact: string | null
  confidence: number | null
  impact: string
  is_hypothesis: boolean
  priority_rank: number | null
  status: string
  suggested_next_mission_json: { objective?: string }
}

export function StrategyPage({ merchantId }: { merchantId: string }) {
  const [recs, setRecs] = useState<RecommendationRow[]>([])

  const refresh = useCallback(() => {
    fetch(`/api/v1/team/merchants/${merchantId}/recommendations?limit=20`)
      .then((r) => r.json())
      .then(setRecs)
      .catch(() => {})
  }, [merchantId])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  const launchFromRec = async (rec: RecommendationRow) => {
    await team.createMission({
      merchant_id: merchantId,
      name: rec.suggested_next_mission_json?.objective?.slice(0, 80) ?? rec.problem.slice(0, 80),
      objective: rec.suggested_next_mission_json?.objective ?? rec.recommendation_text,
      mission_type: 'on_demand',
    })
  }

  return (
    <div className="fade-up space-y-10">
      <section>
        <SectionHead
          eyebrow="strategic queue"
          title="What to do next, ranked."
          support="The Strategy Agent ranks opportunities and threats by impact and confidence. No-evidence recommendations are flagged as hypotheses."
        />
        {recs.filter((r) => r.status !== 'superseded').length === 0 ? (
          <Empty>No recommendations yet — run the baseline first.</Empty>
        ) : (
          <ol className="space-y-3">
            {recs.map((r) => (
              <li key={r.id} className="card p-5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-dim">#{r.priority_rank ?? '—'}</span>
                  <span className={`rounded border px-1.5 py-px font-mono text-[9px] uppercase ${
                    r.impact === 'high' ? 'border-brand/40 bg-brand/10 text-brand' : 'border-edge text-mute'
                  }`}>
                    {r.impact} impact
                  </span>
                  {r.is_hypothesis && (
                    <span className="rounded border border-warn/40 bg-warn/10 px-1.5 py-px font-mono text-[9px] uppercase text-warn">
                      hypothesis
                    </span>
                  )}
                  <Confidence value={r.confidence} />
                </div>
                <h3 className="display mt-2.5 text-[15px] font-semibold text-ink">{r.problem}</h3>
                <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-mute">{r.recommendation_text}</p>
                {r.expected_impact && (
                  <p className="mt-1.5 text-[12px] text-mute">
                    <span className="text-dim">expected impact —</span> {r.expected_impact}
                  </p>
                )}
                {r.suggested_next_mission_json?.objective && (
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-l-2 border-brand/50 pl-3">
                    <p className="text-[12px] text-brand">next mission: {r.suggested_next_mission_json.objective}</p>
                    <button onClick={() => launchFromRec(r)} className="btn-secondary !py-1 !text-[12px]">
                      <Play size={11} /> Launch it
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      <ExperimentsSection merchantId={merchantId} onChanged={refresh} />
    </div>
  )
}

function ExperimentsSection({ merchantId, onChanged }: { merchantId: string; onChanged: () => void }) {
  const [experiments, setExperiments] = useState<ExperimentDetail[]>([])
  const [hypothesis, setHypothesis] = useState('Explicit delivery messaging increases buyer selection')
  const [controlMsg, setControlMsg] = useState('Fast shipping')
  const [treatmentMsg, setTreatmentMsg] = useState('Arrives by Thursday')
  const [cohort, setCohort] = useState(4)
  const [busy, setBusy] = useState(false)

  const loadList = useCallback(() => {
    fetch(`/api/v1/team/merchants/${merchantId}/experiments`)
      .then((r) => r.json())
      .then(setExperiments)
      .catch(() => {})
  }, [merchantId])

  useEffect(() => {
    loadList()
    const t = setInterval(loadList, 5000)
    return () => clearInterval(t)
  }, [loadList])

  const createAndStart = async () => {
    setBusy(true)
    try {
      const { experiment_id } = await team.createExperiment({
        merchant_id: merchantId,
        hypothesis,
        control_variant_json: { product: 'Velocity Sports Revolution 7', messaging: controlMsg },
        treatment_variant_json: { product: 'Velocity Sports Revolution 7', messaging: treatmentMsg },
        cohort_size: cohort,
      })
      await team.startExperiment(experiment_id)
      loadList()
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <SectionHead
        eyebrow="counterfactuals"
        title="Test the fix before you ship it."
        support="Run the same simulated buyer cohort against control vs variant. Every number here is a simulation — never presented as real revenue."
      />

      <Panel>
        <label className="mono-data block" htmlFor="exp-hypothesis">hypothesis</label>
        <input id="exp-hypothesis" value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} className="mt-1.5" />
        <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_100px_auto] sm:items-end">
          <div>
            <label className="mono-data block" htmlFor="exp-control">control (current)</label>
            <input id="exp-control" value={controlMsg} onChange={(e) => setControlMsg(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <label className="mono-data block" htmlFor="exp-treatment">variant (proposed)</label>
            <input id="exp-treatment" value={treatmentMsg} onChange={(e) => setTreatmentMsg(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <label className="mono-data block" htmlFor="exp-cohort">cohort</label>
            <input
              id="exp-cohort"
              type="number"
              min={2}
              max={12}
              value={cohort}
              onChange={(e) => setCohort(Math.max(2, Math.min(12, Number(e.target.value) || 2)))}
              className="mt-1.5"
            />
          </div>
          <button onClick={createAndStart} disabled={busy} className="btn-primary h-[42px] shrink-0">
            <FlaskConical size={14} /> {busy ? 'starting…' : 'Run test'}
          </button>
        </div>
      </Panel>

      <div className="mt-4 space-y-3">
        {experiments.length === 0 && <Empty>No experiments yet.</Empty>}
        {experiments.map((e) => (
          <article key={e.id} className="card p-5">
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip status={e.status} />
              <SimulatedTag small />
              <span className="ml-auto mono-data">cohort {e.cohort_size}</span>
            </div>
            <p className="mt-2 text-[13px] font-medium text-ink">{e.hypothesis}</p>
            {e.result_json?.SIMULATED ? (
              <div className="mt-4 space-y-2 fade-up">
                <StatBar
                  label="control · current"
                  value={Math.round((e.result_json.control_selection_rate ?? 0) * 100)}
                  accent={false}
                  suffix="% pick"
                />
                <StatBar
                  label="variant · proposed"
                  value={Math.round((e.result_json.treatment_selection_rate ?? 0) * 100)}
                  accent
                  suffix="% pick"
                />
                <p className="pt-1 font-mono text-[12px]">
                  <span className={Number(e.result_json.simulated_relative_lift_pct) >= 0 ? 'text-ok' : 'text-danger'}>
                    {(Number(e.result_json.simulated_relative_lift_pct) >= 0 ? '+' : '') +
                      e.result_json.simulated_relative_lift_pct}
                    % simulated lift
                  </span>
                  <span className="text-dim"> — not real production revenue</span>
                </p>
              </div>
            ) : (
              <p className="mono-data mt-2">arms running…</p>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}
