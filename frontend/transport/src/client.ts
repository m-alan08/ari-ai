import { decode, ProtocolError } from './contracts.js';
import type * as C from './contracts.js';

export type Fetch = typeof globalThis.fetch;
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface RequestOptions { signal?: AbortSignal }
/** A create/decide call: `idempotencyKey` makes a retry of the same user action replay instead of duplicating. */
export interface MutationOptions extends RequestOptions { idempotencyKey?: string }
export class ApiError extends Error {
  override name = 'ApiError';
  constructor(readonly status: number, readonly error: C.ErrorBody) { super(error.message); }
}
export interface ClientOptions { baseUrl: string; fetch?: Fetch }
const segment = (id: string): string => {
  if (!/^[A-Za-z0-9_-]+$/.test(id)) throw new ProtocolError('Invalid resource identifier');
  return encodeURIComponent(id);
};

export class ApiClient {
  readonly baseUrl: string;
  readonly fetch: Fetch;
  private csrf: string | undefined;
  constructor(options: ClientOptions) {
    const url = new URL(options.baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new ProtocolError('Expected an HTTP API URL without credentials, query or fragment');
    }
    this.baseUrl = url.href.replace(/\/$/, '');
    this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  }
  clearSession(): void { this.csrf = undefined; }
  private async request(path: string, schema: Parameters<typeof decode>[0] | null | typeof RAW, options: RequestOptions = {}, body?: unknown, key?: string, envelope?: 'assessment', page = false, method: 'POST' | 'PATCH' = 'POST'): Promise<unknown> {
    const headers = new Headers({ Accept: 'application/json' });
    if (body !== undefined) {
      if (!this.csrf && path !== '/auth/dev/login') throw new ProtocolError('Load the authenticated session before a mutation');
      headers.set('Content-Type', 'application/json');
      if (this.csrf) headers.set('X-CSRF-Token', this.csrf);
    }
    if (key !== undefined) {
      if (!key.trim()) throw new ProtocolError('An idempotency key is required');
      headers.set('Idempotency-Key', key);
    }
    const response = await this.fetch(this.baseUrl + path, {
      method: body === undefined ? 'GET' : method, credentials: 'include', headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      ...(options.signal ? { signal: options.signal } : {}),
    });
    if (response.status === 401) this.clearSession();
    if (schema === null && response.ok) {
      if (response.status !== 204) throw new ProtocolError('Expected an empty response');
      await response.body?.cancel();
      return undefined;
    }
    const value = await readJson(response);
    if (!response.ok) throw new ApiError(response.status, decode<C.ErrorResponse>('ErrorResponse', value).error);
    if (schema === null) throw new ProtocolError('Expected an empty response');
    if (schema === RAW) return value;
    if (page) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ProtocolError('Invalid list response');
      const record = value as Record<string, unknown>;
      if (!Array.isArray(record.items) || !(record.next_cursor === null || typeof record.next_cursor === 'string') || Object.keys(record).some(k => k !== 'items' && k !== 'next_cursor')) throw new ProtocolError('Invalid list response');
      return { items: record.items.map(item => decode(schema as Parameters<typeof decode>[0], item)), next_cursor: record.next_cursor };
    }
    if (envelope) {
      if (!value || typeof value !== 'object' || !(envelope in value)) throw new ProtocolError('Invalid assessment acceptance envelope');
      return decode(schema as Parameters<typeof decode>[0], (value as Record<string, unknown>)[envelope]);
    }
    return decode(schema as Parameters<typeof decode>[0], value);
  }
  async session(options?: RequestOptions): Promise<C.MeResponse> {
    this.clearSession();
    const me = await this.request('/me', 'MeResponse', options) as C.MeResponse;
    this.csrf = me.csrf_token;
    return me;
  }
  // Development-only backend endpoint, deliberately never called automatically.
  async devLogin(body: C.DevLoginRequest, options?: RequestOptions): Promise<C.MeResponse> {
    this.clearSession();
    const me = await this.request('/auth/dev/login', 'MeResponse', options, body) as C.MeResponse;
    this.csrf = me.csrf_token;
    return me;
  }
  async logout(options?: RequestOptions): Promise<void> {
    await this.request('/auth/logout', null, options, {});
    this.clearSession();
  }
  private list<T>(path: string, schema: Parameters<typeof decode>[0], options?: RequestOptions): Promise<Page<T>> {
    return this.request(path, schema, options, undefined, undefined, undefined, true) as Promise<Page<T>>;
  }
  projects(options?: RequestOptions): Promise<Page<C.Project>> {
    return this.list('/projects', 'Project', options);
  }
  repositories(projectId: string, options?: RequestOptions): Promise<Page<C.Repository>> {
    return this.list(`/projects/${segment(projectId)}/repositories`, 'Repository', options);
  }
  scopes(projectId: string, options?: RequestOptions): Promise<Page<C.AuthorizedScope>> {
    return this.list(`/projects/${segment(projectId)}/scopes`, 'AuthorizedScope', options);
  }
  /** Newest first, 50 per page. Pass the previous page's `next_cursor` as `cursor` for the next (older) page. */
  assessments(projectId: string, options?: RequestOptions & { cursor?: string }): Promise<Page<C.Assessment>> {
    const base = `/projects/${segment(projectId)}/assessments`;
    const cursor = options?.cursor;
    const path = cursor === undefined ? base : `${base}?cursor=${segment(cursor)}`;
    return this.list(path, 'Assessment', options?.signal ? { signal: options.signal } : undefined);
  }
  agentRuns(assessmentId: string, options?: RequestOptions): Promise<Page<C.AgentRun>> {
    return this.list(`/assessments/${segment(assessmentId)}/runs`, 'AgentRun', options);
  }
  findings(projectId: string, options?: RequestOptions): Promise<Page<C.Finding>> {
    return this.list(`/projects/${segment(projectId)}/findings`, 'Finding', options);
  }
  approvals(projectId: string, options?: RequestOptions): Promise<Page<C.Approval>> {
    return this.list(`/projects/${segment(projectId)}/approvals`, 'Approval', options);
  }
  project(id: string, options?: RequestOptions): Promise<C.Project> {
    return this.request(`/projects/${segment(id)}`, 'Project', options) as Promise<C.Project>;
  }
  createProject(body: C.ProjectCreate, options?: MutationOptions): Promise<C.Project> {
    return this.request('/projects', 'Project', options, body, options?.idempotencyKey) as Promise<C.Project>;
  }
  connectRepository(projectId: string, body: C.RepositoryConnect, options?: RequestOptions): Promise<C.Repository> {
    return this.request(`/projects/${segment(projectId)}/repositories`, 'Repository', options, body) as Promise<C.Repository>;
  }
  repository(repoId: string, options?: RequestOptions): Promise<C.Repository> {
    return this.request(`/repositories/${segment(repoId)}`, 'Repository', options) as Promise<C.Repository>;
  }
  updateRepository(repoId: string, body: C.RepositoryUpdate, options?: RequestOptions): Promise<C.Repository> {
    return this.request(`/repositories/${segment(repoId)}`, 'Repository', options, body, undefined, undefined, false, 'PATCH') as Promise<C.Repository>;
  }
  /** Owner kill-switch: parks the repository server-side; the GitHub App grant stays until uninstalled. */
  disconnectRepository(repoId: string, options?: RequestOptions): Promise<{ id: string; disconnected: boolean }> {
    return this.request(`/repositories/${segment(repoId)}/disconnect`, RAW, options, {}) as Promise<{ id: string; disconnected: boolean }>;
  }
  onboardingStatus(projectId: string, options?: RequestOptions): Promise<C.OnboardingStatus> {
    return this.request(`/projects/${segment(projectId)}/onboarding`, 'OnboardingStatus', options) as Promise<C.OnboardingStatus>;
  }
  /** Owner-only authorized test event through the app's real owned_staging channel. Never executes. */
  onboardingTestEvent(projectId: string, body: C.OnboardingTestEventCreate, options?: MutationOptions): Promise<C.OnboardingTestEventResult> {
    return this.request(`/projects/${segment(projectId)}/onboarding/test-event`, 'OnboardingTestEventResult', options, body, options?.idempotencyKey) as Promise<C.OnboardingTestEventResult>;
  }
  createSnapshot(repoId: string, body: C.SnapshotCreate, key: string, options?: RequestOptions): Promise<C.Snapshot> {
    return this.request(`/repositories/${segment(repoId)}/snapshots`, 'Snapshot', options, body, key) as Promise<C.Snapshot>;
  }
  /** Newest first. `status: 'ready'` lists the snapshots intelligence reads can use. */
  snapshots(repoId: string, query: SnapshotQuery = {}, options?: RequestOptions): Promise<Page<C.Snapshot>> {
    const params = new URLSearchParams();
    if (query.status) params.set('status', query.status);
    if (query.limit !== undefined) params.set('limit', String(query.limit));
    if (query.cursor) params.set('cursor', query.cursor);
    const qs = params.toString();
    return this.list(`/repositories/${segment(repoId)}/snapshots${qs ? `?${qs}` : ''}`, 'Snapshot', options);
  }
  snapshot(id: string, options?: RequestOptions): Promise<C.Snapshot> {
    return this.request(`/snapshots/${segment(id)}`, 'Snapshot', options) as Promise<C.Snapshot>;
  }
  createScope(projectId: string, body: C.ScopeCreate, options?: MutationOptions): Promise<C.AuthorizedScope> {
    return this.request(`/projects/${segment(projectId)}/scopes`, 'AuthorizedScope', options, body, options?.idempotencyKey) as Promise<C.AuthorizedScope>;
  }
  approveScope(id: string, options?: RequestOptions): Promise<C.AuthorizedScope> {
    return this.request(`/scopes/${segment(id)}/approve`, 'AuthorizedScope', options, {}) as Promise<C.AuthorizedScope>;
  }
  startAssessment(projectId: string, body: C.AssessmentCreate, key: string, options?: RequestOptions): Promise<C.Assessment> {
    if (typeof key !== 'string' || !key.trim()) throw new ProtocolError('An idempotency key is required');
    return this.request(`/projects/${segment(projectId)}/assessments`, 'Assessment', options, body, key, 'assessment') as Promise<C.Assessment>;
  }
  assessment(id: string, options?: RequestOptions): Promise<C.AssessmentDetail> {
    return this.request(`/assessments/${segment(id)}`, 'AssessmentDetail', options) as Promise<C.AssessmentDetail>;
  }
  incidents(projectId: string, options?: RequestOptions): Promise<Page<C.Incident>> {
    return this.list(`/projects/${segment(projectId)}/incidents`, 'Incident', options);
  }
  createIncident(projectId: string, body: C.IncidentCreate, options?: MutationOptions): Promise<C.Incident> {
    return this.request(`/projects/${segment(projectId)}/incidents`, 'Incident', options, body, options?.idempotencyKey) as Promise<C.Incident>;
  }
  incident(id: string, options?: RequestOptions): Promise<C.IncidentDetail> {
    return this.request(`/incidents/${segment(id)}`, 'IncidentDetail', options) as Promise<C.IncidentDetail>;
  }
  actOnIncident(id: string, body: C.IncidentAction, options?: MutationOptions): Promise<C.Incident> {
    return this.request(`/incidents/${segment(id)}/actions`, 'Incident', options, body, options?.idempotencyKey) as Promise<C.Incident>;
  }
  decideApproval(id: string, body: C.ApprovalDecision, options?: MutationOptions): Promise<C.Approval> {
    return this.request(`/approvals/${segment(id)}/decision`, 'Approval', options, body, options?.idempotencyKey) as Promise<C.Approval>;
  }
  revokeScope(id: string, options?: RequestOptions): Promise<C.AuthorizedScope> {
    return this.request(`/scopes/${segment(id)}/revoke`, 'AuthorizedScope', options, {}) as Promise<C.AuthorizedScope>;
  }
  eventsLog(assessmentId: string, after = 0, options?: RequestOptions): Promise<Page<C.StreamEvent>> {
    if (!Number.isSafeInteger(after) || after < 0) throw new ProtocolError('Invalid event cursor');
    return this.list(`/assessments/${segment(assessmentId)}/events/log?after=${after}`, 'StreamEvent', options);
  }
  async cancelAssessment(id: string, options?: RequestOptions): Promise<CancelResult> {
    const value = await this.request(`/assessments/${segment(id)}/cancel`, RAW, options, {});
    const record = object(value, 'cancel response');
    if (record.assessment_id !== id || typeof record.cancelled !== 'boolean' || record.cancel_requested !== true) {
      throw new ProtocolError('Invalid cancel response');
    }
    return { assessment_id: id, cancelled: record.cancelled, cancel_requested: true };
  }
  async finding(id: string, options?: RequestOptions): Promise<FindingDetail> {
    const record = object(await this.request(`/findings/${segment(id)}`, RAW, options), 'finding response');
    if (!Array.isArray(record.records) || Object.keys(record).some(k => k !== 'finding' && k !== 'records')) {
      throw new ProtocolError('Invalid finding response');
    }
    const finding = decode<C.Finding>('Finding', record.finding);
    const records = record.records.map(item => {
      const entry = object(item, 'finding record');
      const type = entry.type;
      if (typeof type !== 'string' || !(type in RECORD_SCHEMAS)) throw new ProtocolError('Unknown finding record type');
      const name = RECORD_SCHEMAS[type as FindingRecord['type']];
      return { type, record: decode(name, entry.record) } as FindingRecord;
    });
    return { finding, records };
  }
  /** Report assembled by the backend only from persisted records (shared AssessmentReport). */
  report(assessmentId: string, options?: RequestOptions): Promise<C.AssessmentReport> {
    return this.request(`/assessments/${segment(assessmentId)}/report`, 'AssessmentReport', options) as Promise<C.AssessmentReport>;
  }
  patches(findingId: string, options?: RequestOptions): Promise<Page<C.PatchView>> {
    return this.list(`/findings/${segment(findingId)}/patches`, 'PatchView', options);
  }
  acceptRisk(findingId: string, body: C.AcceptRisk, options?: RequestOptions): Promise<C.Finding> {
    return this.request(`/findings/${segment(findingId)}/accept-risk`, 'Finding', options, body) as Promise<C.Finding>;
  }
  pendingApprovals(projectId: string, options?: RequestOptions): Promise<Page<C.Approval>> {
    return this.list(`/projects/${segment(projectId)}/approvals?status=pending`, 'Approval', options);
  }
  /** Newest first. Pass the returned `next_cursor` as `cursor` for older events. */
  monitoringEvents(projectId: string, options: RequestOptions & MonitoringQuery = {}): Promise<Page<C.MonitoringEvent>> {
    const query = options;
    const params = new URLSearchParams();
    if (query.limit !== undefined) {
      if (!Number.isSafeInteger(query.limit) || query.limit < 1 || query.limit > 200) throw new ProtocolError('Invalid page size');
      params.set('limit', String(query.limit));
    }
    if (query.cursor) params.set('cursor', query.cursor);
    const qs = params.toString();
    return this.list(`/projects/${segment(projectId)}/events${qs ? `?${qs}` : ''}`, 'MonitoringEvent', options);
  }
  reassessments(projectId: string, options?: RequestOptions): Promise<Page<C.ReassessmentRequest>> {
    return this.list(`/projects/${segment(projectId)}/reassessments`, 'ReassessmentRequest', options);
  }
  /** Newest first. Pass the returned `next_cursor` as `cursor` for older recommendations. */
  async defenseRecommendations(projectId: string, options?: RequestOptions & { cursor?: string }): Promise<LabeledPage<C.DefenseRecommendation>> {
    const qs = options?.cursor ? `?cursor=${encodeURIComponent(options.cursor)}` : '';
    const record = object(await this.request(`/projects/${segment(projectId)}/defense-recommendations${qs}`, RAW, options), 'recommendation list');
    const cursor = record.next_cursor;
    if (!Array.isArray(record.items) || (cursor !== null && (typeof cursor !== 'string' || cursor.length === 0 || cursor.length > 200)) || typeof record.label !== 'string' || Object.keys(record).length !== 3) {
      throw new ProtocolError('Invalid recommendation list');
    }
    return { items: record.items.map(i => decode<C.DefenseRecommendation>('DefenseRecommendation', i)), next_cursor: cursor, label: record.label };
  }
  recommendationExplanation(id: string, options?: RequestOptions): Promise<C.RLExplanation> {
    return this.request(`/defense-recommendations/${segment(id)}/explanation`, 'RLExplanation', options) as Promise<C.RLExplanation>;
  }
  connectedApplications(projectId: string, options?: RequestOptions): Promise<Page<C.ConnectedApplication>> {
    return this.list(`/projects/${segment(projectId)}/applications`, 'ConnectedApplication', options);
  }
  createConnectedApplication(projectId: string, body: C.ConnectedApplicationCreate, options?: RequestOptions): Promise<C.ConnectedApplication> {
    return this.request(`/projects/${segment(projectId)}/applications`, 'ConnectedApplication', options, body) as Promise<C.ConnectedApplication>;
  }
  connectedApplication(id: string, options?: RequestOptions): Promise<C.ConnectedApplication> {
    return this.request(`/applications/${segment(id)}`, 'ConnectedApplication', options) as Promise<C.ConnectedApplication>;
  }
  createApplicationIngestToken(appId: string, options?: RequestOptions): Promise<C.IngestTokenCreated> {
    return this.request(`/applications/${segment(appId)}/ingest-tokens`, 'IngestTokenCreated', options, {}) as Promise<C.IngestTokenCreated>;
  }
  ingestTokens(projectId: string, options?: RequestOptions): Promise<Page<C.IngestTokenView>> {
    return this.list(`/projects/${segment(projectId)}/ingest-tokens`, 'IngestTokenView', options);
  }
  revokeIngestToken(tokenId: string, options?: RequestOptions): Promise<void> {
    return this.request(`/ingest-tokens/${segment(tokenId)}/revoke`, null, options, {}) as Promise<void>;
  }
  rotateApplicationIngestToken(appId: string, options?: RequestOptions): Promise<C.IngestTokenRotated> {
    return this.request(`/applications/${segment(appId)}/ingest-tokens/rotate`, 'IngestTokenRotated', options, {}) as Promise<C.IngestTokenRotated>;
  }
  disconnectConnectedApplication(appId: string, options?: RequestOptions): Promise<C.ConnectedApplication> {
    return this.request(`/applications/${segment(appId)}/disconnect`, 'ConnectedApplication', options, {}) as Promise<C.ConnectedApplication>;
  }
  shadowVerifyConnectedApplication(appId: string, options?: RequestOptions): Promise<C.ConnectedApplication> {
    return this.request(`/applications/${segment(appId)}/shadow-verify`, 'ConnectedApplication', options, {}) as Promise<C.ConnectedApplication>;
  }
  pilotReport(projectId: string, options?: RequestOptions): Promise<C.PilotReport> {
    return this.request(`/projects/${segment(projectId)}/pilot-report`, 'PilotReport', options) as Promise<C.PilotReport>;
  }
  defenseHealth(projectId: string, options?: RequestOptions): Promise<C.DefenseHealth> {
    return this.request(`/projects/${segment(projectId)}/defense-health`, 'DefenseHealth', options) as Promise<C.DefenseHealth>;
  }
  defenseGraph(projectId: string, options?: RequestOptions): Promise<C.ComponentGraph> {
    return this.request(`/projects/${segment(projectId)}/defense-graph`, 'ComponentGraph', options) as Promise<C.ComponentGraph>;
  }
  recommendationReviews(id: string, options?: RequestOptions): Promise<Page<C.AnalystReview>> {
    return this.list(`/defense-recommendations/${segment(id)}/reviews`, 'AnalystReview', options);
  }
  projectDefenseReviews(projectId: string, options?: RequestOptions): Promise<Page<C.AnalystReview>> {
    return this.list(`/projects/${segment(projectId)}/defense-reviews`, 'AnalystReview', options);
  }
  createRecommendationReview(id: string, body: C.AnalystReviewCreate, options?: RequestOptions): Promise<C.AnalystReview> {
    return this.request(`/defense-recommendations/${segment(id)}/reviews`, 'AnalystReview', options, body) as Promise<C.AnalystReview>;
  }
  /** Newest first. Pass `next_cursor` as `cursor` for older shadow decisions. */
  async livePolicyDecisions(projectId: string, options?: RequestOptions & { cursor?: string }): Promise<LabeledPage<C.LivePolicyDecision>> {
    const qs = options?.cursor ? `?cursor=${encodeURIComponent(options.cursor)}` : '';
    const record = object(await this.request(`/projects/${segment(projectId)}/live-policy-decisions${qs}`, RAW, options), 'live policy decision list');
    const cursor = record.next_cursor;
    if (!Array.isArray(record.items) || (cursor !== null && (typeof cursor !== 'string' || cursor.length === 0 || cursor.length > 200)) || typeof record.label !== 'string' || Object.keys(record).length !== 3) {
      throw new ProtocolError('Invalid live policy decision list');
    }
    return { items: record.items.map(i => decode<C.LivePolicyDecision>('LivePolicyDecision', i)), next_cursor: cursor, label: record.label };
  }
  livePolicyDecision(id: string, options?: RequestOptions): Promise<C.LivePolicyDecision> {
    return this.request(`/live-policy-decisions/${segment(id)}`, 'LivePolicyDecision', options) as Promise<C.LivePolicyDecision>;
  }
  livePolicyDecisionReviews(id: string, options?: RequestOptions): Promise<Page<C.AnalystReview>> {
    return this.list(`/live-policy-decisions/${segment(id)}/reviews`, 'AnalystReview', options);
  }
  createLivePolicyDecisionReview(id: string, body: C.AnalystReviewCreate, options?: RequestOptions): Promise<C.AnalystReview> {
    return this.request(`/live-policy-decisions/${segment(id)}/reviews`, 'AnalystReview', options, body) as Promise<C.AnalystReview>;
  }
  controlGrants(projectId: string, options?: RequestOptions): Promise<Page<C.ControlGrant>> {
    return this.list(`/projects/${segment(projectId)}/control-grants`, 'ControlGrant', options);
  }
  createControlGrant(projectId: string, body: C.ControlGrantCreate, options?: RequestOptions): Promise<C.ControlGrant> {
    return this.request(`/projects/${segment(projectId)}/control-grants`, 'ControlGrant', options, body) as Promise<C.ControlGrant>;
  }
  revokeControlGrant(id: string, options?: RequestOptions): Promise<C.ControlGrant> {
    return this.request(`/control-grants/${segment(id)}/revoke`, 'ControlGrant', options, {}) as Promise<C.ControlGrant>;
  }
  createControlChannel(appId: string, body: C.ControlChannelCreate, options?: RequestOptions): Promise<C.ControlChannelCreated> {
    return this.request(`/applications/${segment(appId)}/control-channel`, 'ControlChannelCreated', options, body) as Promise<C.ControlChannelCreated>;
  }
  controlChannel(appId: string, options?: RequestOptions): Promise<C.ControlChannelView> {
    return this.request(`/applications/${segment(appId)}/control-channel`, 'ControlChannelView', options) as Promise<C.ControlChannelView>;
  }
  authorizeControl(appId: string, body: C.ControlAuthorizationCreate, options?: RequestOptions): Promise<C.LiveControl> {
    return this.request(`/applications/${segment(appId)}/control-authorizations`, 'LiveControl', options, body) as Promise<C.LiveControl>;
  }
  async liveControls(projectId: string, options?: RequestOptions): Promise<LabeledPage<C.LiveControl>> {
    const record = object(await this.request(`/projects/${segment(projectId)}/live-controls`, RAW, options), 'live control list');
    const cursor = record.next_cursor;
    if (!Array.isArray(record.items) || (cursor !== null && (typeof cursor !== 'string' || cursor.length === 0 || cursor.length > 200)) || typeof record.label !== 'string' || Object.keys(record).length !== 3) {
      throw new ProtocolError('Invalid live control list');
    }
    return { items: record.items.map(i => decode<C.LiveControl>('LiveControl', i)), next_cursor: cursor, label: record.label };
  }
  liveControl(id: string, options?: RequestOptions): Promise<C.LiveControl> {
    return this.request(`/live-controls/${segment(id)}`, 'LiveControl', options) as Promise<C.LiveControl>;
  }
  rollbackLiveControl(id: string, body: C.LiveControlRollbackCreate, options?: RequestOptions): Promise<C.LiveControl> {
    return this.request(`/live-controls/${segment(id)}/rollback`, 'LiveControl', options, body) as Promise<C.LiveControl>;
  }
  async emergencyStop(projectId: string, options?: RequestOptions): Promise<{ items: C.EmergencyStop[]; next_cursor: string | null; active: boolean }> {
    const record = object(await this.request(`/projects/${segment(projectId)}/emergency-stop`, RAW, options), 'emergency stop');
    if (!Array.isArray(record.items) || record.next_cursor !== null && (typeof record.next_cursor !== 'string') || typeof record.active !== 'boolean' || Object.keys(record).length !== 3) {
      throw new ProtocolError('Invalid emergency stop list');
    }
    return { items: record.items.map(i => decode<C.EmergencyStop>('EmergencyStop', i)), next_cursor: record.next_cursor as string | null, active: record.active };
  }
  createEmergencyStop(projectId: string, body: C.EmergencyStopCreate, options?: RequestOptions): Promise<C.EmergencyStop> {
    return this.request(`/projects/${segment(projectId)}/emergency-stop`, 'EmergencyStop', options, body) as Promise<C.EmergencyStop>;
  }
  clearEmergencyStop(id: string, options?: RequestOptions): Promise<C.EmergencyStop> {
    return this.request(`/emergency-stop/${segment(id)}/clear`, 'EmergencyStop', options, {}) as Promise<C.EmergencyStop>;
  }
  firstResponderMode(projectId: string, options?: RequestOptions): Promise<C.FirstResponderModeView> {
    return this.request(`/projects/${segment(projectId)}/first-responder/mode`, 'FirstResponderModeView', options) as Promise<C.FirstResponderModeView>;
  }
  setFirstResponderMode(projectId: string, body: C.FirstResponderModeUpdate, options?: RequestOptions): Promise<C.FirstResponderModeView> {
    return this.request(`/projects/${segment(projectId)}/first-responder/mode`, 'FirstResponderModeView', options, body) as Promise<C.FirstResponderModeView>;
  }
  firstResponderPermissions(appId: string, options?: RequestOptions): Promise<C.FirstResponderEffectivePermissions> {
    return this.request(`/applications/${segment(appId)}/first-responder/permissions`, 'FirstResponderEffectivePermissions', options) as Promise<C.FirstResponderEffectivePermissions>;
  }
  firstResponderCapabilities(projectId: string, options?: RequestOptions): Promise<C.AgentCapabilityRegistry> {
    return this.request(`/projects/${segment(projectId)}/first-responder/capabilities`, 'AgentCapabilityRegistry', options) as Promise<C.AgentCapabilityRegistry>;
  }
  async firstResponderDecisions(projectId: string, options?: RequestOptions): Promise<LabeledPage<C.FirstResponderDecision>> {
    const record = object(await this.request(`/projects/${segment(projectId)}/first-responder/decisions`, RAW, options), 'first-responder decision list');
    const cursor = record.next_cursor;
    if (!Array.isArray(record.items) || (cursor !== null && (typeof cursor !== 'string' || cursor.length === 0 || cursor.length > 200)) || typeof record.label !== 'string' || Object.keys(record).length !== 3) {
      throw new ProtocolError('Invalid first-responder decision list');
    }
    return { items: record.items.map(i => decode<C.FirstResponderDecision>('FirstResponderDecision', i)), next_cursor: cursor, label: record.label };
  }
  firstResponderInvocations(projectId: string, options?: RequestOptions): Promise<Page<C.FirstResponderInvocation>> {
    return this.list(`/projects/${segment(projectId)}/first-responder/invocations`, 'FirstResponderInvocation', options);
  }
  firstResponderHandoffs(projectId: string, options?: RequestOptions): Promise<Page<C.AnalystHandoff>> {
    return this.list(`/projects/${segment(projectId)}/first-responder/handoffs`, 'AnalystHandoff', options);
  }
  createDeployCommitMapping(appId: string, body: C.DeployCommitMappingCreate, options?: RequestOptions): Promise<C.DeployCommitMapping> {
    return this.request(`/applications/${segment(appId)}/deploy-mappings`, 'DeployCommitMapping', options, body) as Promise<C.DeployCommitMapping>;
  }
  ackHandoffDelivery(id: string, body: C.AnalystHandoffDeliveryAck, options?: RequestOptions): Promise<C.AnalystHandoff> {
    return this.request(`/first-responder/handoffs/${segment(id)}/delivery-ack`, 'AnalystHandoff', options, body) as Promise<C.AnalystHandoff>;
  }
  /** Repositories the user can connect through the GitHub App, and the App's install link. */
  async githubRepositories(options?: RequestOptions): Promise<AccessibleRepositories> {
    const record = object(await this.request('/github/repositories', RAW, options), 'repository list');
    const install = record.install_url;
    if (!Array.isArray(record.items) || !(install === null || (typeof install === 'string' && /^https:\/\//.test(install))) || record.next_cursor !== null) {
      throw new ProtocolError('Invalid repository list');
    }
    const items = record.items.map(raw => {
      const r = object(raw, 'repository');
      if (typeof r.full_name !== 'string' || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(r.full_name)
        || !Number.isSafeInteger(r.installation_id) || typeof r.default_branch !== 'string'
        || typeof r.private !== 'boolean' || !(r.connected_repo_id === null || typeof r.connected_repo_id === 'string')) {
        throw new ProtocolError('Invalid repository list');
      }
      return { full_name: r.full_name, installation_id: r.installation_id as number, default_branch: r.default_branch, private: r.private, connected_repo_id: r.connected_repo_id as string | null };
    });
    return { items, install_url: install as string | null };
  }
  /** Recent commits on a connected repository's default branch, read live from GitHub. */
  async repositoryCommits(repoId: string, options?: RequestOptions): Promise<RepositoryCommits> {
    const record = object(await this.request(`/repositories/${segment(repoId)}/commits`, RAW, options), 'commit list');
    if (!Array.isArray(record.items) || typeof record.available !== 'boolean' || !(record.reason === null || typeof record.reason === 'string') || record.next_cursor !== null) {
      throw new ProtocolError('Invalid commit list');
    }
    const items = record.items.map(raw => {
      const c = object(raw, 'commit');
      if (typeof c.sha !== 'string' || !/^[0-9a-f]{40}$/.test(c.sha) || typeof c.message !== 'string' || typeof c.author !== 'string'
        || !(c.committed_at === null || typeof c.committed_at === 'string')
        || !(c.html_url === null || (typeof c.html_url === 'string' && /^https:\/\//.test(c.html_url)))) {
        throw new ProtocolError('Invalid commit list');
      }
      return { sha: c.sha, message: c.message, author: c.author, committed_at: c.committed_at as string | null, html_url: c.html_url as string | null };
    });
    return { items, available: record.available, reason: record.reason as string | null };
  }
  /** Start a labelled simulated attack drill; returns the job to poll. */
  async startAttackDrill(projectId: string, options?: RequestOptions): Promise<{ job_id: string }> {
    const r = object(await this.request(`/projects/${segment(projectId)}/attack-drills`, RAW, options, {}), 'drill');
    if (typeof r.job_id !== 'string' || r.simulated !== true) throw new ProtocolError('Invalid drill response');
    return { job_id: r.job_id };
  }
  /** A background job's state; `result` is returned as-is for the caller to validate. */
  async job(id: string, options?: RequestOptions): Promise<JobState> {
    const r = object(await this.request(`/jobs/${segment(id)}`, RAW, options), 'job');
    if (typeof r.id !== 'string' || typeof r.kind !== 'string' || typeof r.status !== 'string') throw new ProtocolError('Invalid job');
    const err = r.last_error === null || r.last_error === undefined ? null : object(r.last_error, 'job error');
    return { id: r.id, kind: r.kind, status: r.status, result: r.result ?? null, error: err && typeof err.message === 'string' ? err.message : null };
  }
  rlEvaluations(projectId: string, options?: RequestOptions): Promise<Page<C.RLEvaluation>> {
    return this.list(`/projects/${segment(projectId)}/rl-evaluations`, 'RLEvaluation', options);
  }
  expectations(snapshotId: string, query: ExpectationQuery = {}, options?: RequestOptions): Promise<Page<C.BehavioralExpectation>> {
    const params = new URLSearchParams();
    if (query.subject !== undefined) params.set('subject', query.subject);
    if (query.limit !== undefined) {
      if (!Number.isSafeInteger(query.limit) || query.limit < 1 || query.limit > 500) throw new ProtocolError('Invalid page size');
      params.set('limit', String(query.limit));
    }
    if (query.cursor !== undefined) {
      if (!/^\d+$/.test(query.cursor)) throw new ProtocolError('Invalid expectation cursor');
      params.set('offset', query.cursor);
    }
    const qs = params.toString();
    return this.list(`/snapshots/${segment(snapshotId)}/expectations${qs ? `?${qs}` : ''}`, 'BehavioralExpectation', options);
  }
  /** Authenticated human intent decision; `expected_revision` gives optimistic concurrency (409 on a stale view). */
  decideExpectation(snapshotId: string, expectationId: string, body: C.ExpectationDecisionRequest, options?: RequestOptions): Promise<C.ExpectationDecision> {
    return this.request(`/snapshots/${segment(snapshotId)}/expectations/${segment(expectationId)}`, 'ExpectationDecision', options, body, undefined, undefined, false, 'PATCH') as Promise<C.ExpectationDecision>;
  }
  expectationDecisions(snapshotId: string, expectationId: string, options?: RequestOptions): Promise<Page<C.ExpectationDecision>> {
    return this.list(`/snapshots/${segment(snapshotId)}/expectations/${segment(expectationId)}/decisions`, 'ExpectationDecision', options);
  }
  /** Owner-only. Settings are replaced as a whole object, never merged field-by-field. */
  updateProject(id: string, body: C.ProjectUpdate, options?: RequestOptions): Promise<C.Project> {
    return this.request(`/projects/${segment(id)}`, 'Project', options, body, undefined, undefined, false, 'PATCH') as Promise<C.Project>;
  }
  /** Bounded text artifact (diffs, logs). The SHA-256 header is verified against the bytes. */
  async artifactText(id: string, options: RequestOptions & { maxBytes?: number } = {}): Promise<ArtifactText> {
    const response = await this.fetch(`${this.baseUrl}/artifacts/${segment(id)}`, {
      method: 'GET', credentials: 'include', headers: { Accept: '*/*' },
      ...(options.signal ? { signal: options.signal } : {}),
    });
    if (response.status === 401) this.clearSession();
    if (!response.ok) throw new ApiError(response.status, decode<C.ErrorResponse>('ErrorResponse', await readJson(response)).error);
    const bytes = await readBytes(response, options.maxBytes ?? 2 * 1024 * 1024);
    const declared = response.headers.get('x-artifact-sha256')?.toLowerCase() ?? null;
    const actual = await sha256Hex(bytes);
    if (declared !== null && declared !== actual) throw new ProtocolError('Artifact hash mismatch');
    let text: string;
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
    catch { throw new ProtocolError('Artifact is not UTF-8 text'); }
    return { id, text, sha256: actual, hashVerified: declared !== null, mediaType: response.headers.get('content-type') ?? 'application/octet-stream' };
  }
}

export interface ExpectationQuery { subject?: string; limit?: number; cursor?: string }
export interface MonitoringQuery { limit?: number; cursor?: string }
export interface SnapshotQuery { status?: C.SnapshotStatus; limit?: number; cursor?: string }
export interface CancelResult { assessment_id: string; cancelled: boolean; cancel_requested: true }
export interface LabeledPage<T> extends Page<T> { label: string }
/** A repository the signed-in user can connect through the GitHub App (captured at sign-in). */
export interface AccessibleRepository {
  full_name: string;
  installation_id: number;
  default_branch: string;
  private: boolean;
  connected_repo_id: string | null;
}
export interface JobState { id: string; kind: string; status: string; result: unknown; error: string | null }
export interface AccessibleRepositories { items: AccessibleRepository[]; install_url: string | null }
export interface RepositoryCommit { sha: string; message: string; author: string; committed_at: string | null; html_url: string | null }
export interface RepositoryCommits { items: RepositoryCommit[]; available: boolean; reason: string | null }
export interface ArtifactText { id: string; text: string; sha256: string; hashVerified: boolean; mediaType: string }
export type FindingRecord =
  | { type: 'hypothesis'; record: C.Hypothesis }
  | { type: 'validation_result'; record: C.ValidationResult }
  | { type: 'attack_path'; record: C.AttackPath }
  | { type: 'root_cause'; record: C.RootCause }
  | { type: 'remediation_plan'; record: C.RemediationPlan }
  | { type: 'hardening'; record: C.HardeningRecommendation };
export interface FindingDetail { finding: C.Finding; records: FindingRecord[] }
// Mirrors backend host._RECORD_TYPES; an unknown type is rejected rather than rendered unvalidated.
const RECORD_SCHEMAS = {
  hypothesis: 'Hypothesis', validation_result: 'ValidationResult', attack_path: 'AttackPath',
  root_cause: 'RootCause', remediation_plan: 'RemediationPlan', hardening: 'HardeningRecommendation',
} as const satisfies Record<FindingRecord['type'], Parameters<typeof decode>[0]>;
// Sentinel: return the bounded JSON body for an endpoint whose envelope is not a shared DTO.
const RAW = '__raw__' as const;
function object(value: unknown, what: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ProtocolError(`Invalid ${what}`);
  return value as Record<string, unknown>;
}

async function readBytes(response: Response, maxBytes: number): Promise<Uint8Array> {
  const reader = response.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) throw new ProtocolError('Artifact exceeds size limit');
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally { reader.releaseLock(); }
  const out = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { out.set(chunk, offset); offset += chunk.byteLength; }
  return out;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes as BufferSource);
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

// Bounded reads prevent an error page or malicious stream from exhausting memory.
export async function readJson(response: Response, maxBytes = 8 * 1024 * 1024): Promise<unknown> {
  if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) {
    await response.body?.cancel();
    throw new ProtocolError('Expected a JSON response');
  }
  const reader = response.body?.getReader();
  if (!reader) throw new ProtocolError('Missing response body');
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let size = 0;
  let text = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) throw new ProtocolError('JSON response exceeds size limit');
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    return JSON.parse(text) as unknown;
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    if (error instanceof ProtocolError || (error instanceof Error && error.name === 'AbortError')) throw error;
    throw new ProtocolError('Invalid JSON response');
  } finally { reader.releaseLock(); }
}
