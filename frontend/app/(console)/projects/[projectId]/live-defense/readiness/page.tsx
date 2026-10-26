'use client';

import { useSearchParams } from 'next/navigation';
import { ReadinessDashboard } from '@/components/live-defense/readiness';
import { useProject } from '@/components/project';
import { api } from '@/lib/api';
import { liveDefenseMode } from '@/lib/live-defense';
import { useResource } from '@/lib/use-resource';

export default function LiveDefenseReadinessPage() {
  const { project } = useProject();
  const mode = liveDefenseMode(useSearchParams().get('preview'));
  const live = mode === 'live';
  const readiness = useResource(live ? `ld-ready:${project.id}` : null, s => api().rlReadiness(project.id, { signal: s }), 15000);
  return <ReadinessDashboard mode={mode} projectId={project.id} readiness={live ? readiness : undefined} />;
}
