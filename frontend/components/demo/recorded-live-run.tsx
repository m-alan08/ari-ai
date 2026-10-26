import { RECORDED_LIVE_RUN as RUN } from '@/lib/demo/recorded-live-run';

/** Real evidence beside the simulated walkthrough: the recorded live M3 run, clearly labelled as a replay. */
export function RecordedLiveRun() {
  return (
    <section className="mt-10 min-w-0 rounded-2xl border border-line bg-panel p-5 sm:p-6" data-testid="recorded-live-run">
      <p className="label-mono text-ok">Recorded live run · executed evidence</p>
      <h2 className="mt-2 break-words text-xl font-semibold tracking-tight">Replay of recorded run {RUN.assessmentId}</h2>
      <p className="mt-1.5 text-sm text-muted">
        All seven agents ran on {RUN.model} against {RUN.target} on {RUN.recordedAt}, at commit {RUN.commit}. This is a
        recording of that run, not a run happening now. Cost ${RUN.usd.toFixed(2)} (cap ${RUN.capUsd}), {Math.round(RUN.durationSeconds / 60)} minutes.
      </p>
      <p className="mt-3 font-mono text-xs text-faint" data-testid="recorded-states">{RUN.states.join(' → ')}</p>
      <div className="mt-4 min-w-0 overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-sm">
        <thead className="text-xs text-muted">
          <tr><th className="py-1 font-medium">Agent</th><th className="font-medium">Result</th><th className="font-medium">Model turns</th><th className="font-medium">Tool calls</th><th className="font-medium">Cost</th></tr>
        </thead>
        <tbody>
          {RUN.agents.map(a => (
            <tr key={a.agent} className="border-t border-line" data-testid={`recorded-agent-${a.agent.toLowerCase()}`}>
              <td className="py-1.5 font-medium">{a.agent}</td><td>{a.status}</td><td>{a.turns}</td><td>{a.tools}</td><td>${a.usd.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <ul className="mt-4 flex flex-col gap-2 text-sm">
        {RUN.findings.map(f => (
          <li key={f.cwe}><span className="font-medium">{f.cwe}</span> · {f.title}<span className="block text-muted">{f.outcome}</span></li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-faint">Limits: {RUN.limitations.join('; ')}.</p>
    </section>
  );
}
