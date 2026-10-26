'use client';

import { useSearchParams } from 'next/navigation';
import { FirstResponder } from '@/components/live-defense/first-responder';
import { useProject } from '@/components/project';
import { api } from '@/lib/api';
import { liveDefenseMode } from '@/lib/live-defense';
import { useResource } from '@/lib/use-resource';

export default function LiveDefenseFirstResponderPage() {
  const { project } = useProject();
  const mode = liveDefenseMode(useSearchParams().get('preview'));
  const live = mode === 'live';
  const apps = useResource(live ? `ld-apps:${project.id}` : null, s => api().connectedApplications(project.id, { signal: s }));
  const frMode = useResource(live ? `ld-fr-mode:${project.id}` : null, s => api().firstResponderMode(project.id, { signal: s }));
  const decisions = useResource(live ? `ld-fr-dec:${project.id}` : null, s => api().firstResponderDecisions(project.id, { signal: s }), 10000);
  const invocations = useResource(live ? `ld-fr-inv:${project.id}` : null, s => api().firstResponderInvocations(project.id, { signal: s }), 10000);
  const handoffs = useResource(live ? `ld-fr-ho:${project.id}` : null, s => api().firstResponderHandoffs(project.id, { signal: s }), 10000);
  const capabilities = useResource(live ? `ld-fr-cap:${project.id}` : null, s => api().firstResponderCapabilities(project.id, { signal: s }));
  const reload = () => {
    apps.reload();
    frMode.reload();
    decisions.reload();
    invocations.reload();
    handoffs.reload();
    capabilities.reload();
  };
  return (
    <FirstResponder
      mode={mode}
      projectId={project.id}
      apps={live ? apps : undefined}
      frMode={live ? frMode : undefined}
      decisions={live ? decisions : undefined}
      invocations={live ? invocations : undefined}
      handoffs={live ? handoffs : undefined}
      capabilities={live ? capabilities : undefined}
      onChange={reload}
    />
  );
}
