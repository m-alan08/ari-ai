'use client';

type CheckDay = { date: string; completed: number; active: number; failed: number };
type Segment = 'completed' | 'active' | 'failed';
const SEGMENTS: { name: Segment; label: string; color: string }[] = [
  { name: 'completed', label: 'Completed', color: 'var(--color-ok)' },
  { name: 'active', label: 'Active', color: 'var(--color-warn)' },
  { name: 'failed', label: 'Failed', color: 'var(--color-bad)' },
];

export function ChecksChart({ days }: { days: CheckDay[] }) {
  const totals = days.map(day => day.completed + day.active + day.failed);
  const max = Math.max(1, ...totals);
  const totalsBySegment = Object.fromEntries(SEGMENTS.map(segment => [
    segment.name,
    days.reduce((sum, day) => sum + day[segment.name], 0),
  ])) as Record<Segment, number>;
  const tickIndexes = [0, 5, 10, 15, 20, 25, 29];
  const label = `30-day check activity: ${totalsBySegment.completed} completed, ${totalsBySegment.active} active, ${totalsBySegment.failed} failed.`;

  return (
    <>
      <div role="img" aria-label={label} className="relative h-36 min-w-0">
        {[0, 50, 100].map(position => <span key={position} aria-hidden="true" className="pointer-events-none absolute inset-x-0 border-t border-line" style={{ top: `${position}%` }} />)}
        <span aria-hidden="true" className="absolute -top-1 left-0 z-10 text-xs text-faint">{max}</span>
        <span aria-hidden="true" className="absolute -bottom-1 left-0 z-10 text-xs text-faint">0</span>
        <div className="absolute inset-0 flex gap-px sm:gap-1">
          {days.map(day => (
            <div key={day.date} data-testid="check-day-column" className="relative flex min-w-0 flex-1 flex-col-reverse justify-start">
              {SEGMENTS.map(segment => {
                const value = day[segment.name];
                return value === 0 ? null : (
                  <span
                    key={segment.name}
                    data-testid={`check-segment-${segment.name}`}
                    className="relative z-[1] w-full shrink-0"
                    style={{ height: `${value / max * 100}%`, backgroundColor: segment.color }}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <div aria-hidden="true" className="relative h-6 text-xs text-faint">
        {tickIndexes.map(index => {
          const day = days[index];
          if (!day) return null;
          const date = new Date(`${day.date}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
          const edgeClass = index === 0 ? 'left-0' : index === 29 ? 'right-0' : 'left-1/2 -translate-x-1/2';
          const mobileHidden = index === 5 || index === 15 || index === 25 ? 'hidden sm:block' : '';
          return <span key={index} className={`absolute top-1 whitespace-nowrap font-mono ${edgeClass} ${mobileHidden}`} style={index !== 0 && index !== 29 ? { left: `${(index + 0.5) / 30 * 100}%` } : undefined}>{date}</span>;
        })}
      </div>
      <div aria-hidden="true" className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
        {SEGMENTS.map(segment => <span key={segment.name} className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ backgroundColor: segment.color }} />{segment.label}</span>)}
      </div>
      <div className="sr-only">
        <table>
          <caption>Check outcomes for the last 30 UTC days</caption>
          <thead><tr><th scope="col">Date</th><th scope="col">Completed</th><th scope="col">Active</th><th scope="col">Failed</th></tr></thead>
          <tbody>{days.map(day => <tr key={day.date}><th scope="row">{day.date}</th><td>{day.completed}</td><td>{day.active}</td><td>{day.failed}</td></tr>)}</tbody>
        </table>
      </div>
    </>
  );
}
