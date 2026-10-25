'use client';

import { useSearchParams } from 'next/navigation';
import { Executions } from '@/components/live-defense/executions';
import { useProject } from '@/components/project';
import { api } from '@/lib/api';
import { liveDefenseMode } from '@/lib/live-defense';
import { useResource } from '@/lib/use-resource';

export default function LiveDefenseExecutionsPage() {
  const { project } = useProject();
  const mode = liveDefenseMode(useSearchParams().get('preview'));
  const live = mode === 'live';
  const diagnostics = useResource(
    live ? `ld-diag:${project.id}` : null,
    s => api().firstResponderDiagnostics(project.id, { signal: s }),
    15000,
  );
  return <Executions mode={mode} projectId={project.id} diagnostics={live ? diagnostics : undefined} />;
}
