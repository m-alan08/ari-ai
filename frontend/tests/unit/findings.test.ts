import { describe, expect, it } from 'vitest';
import { findingGroup, findingGroupCounts } from '@/lib/findings';

describe('finding groups', () => {
  it('does not call an inconclusive or rejected fix validated', () => {
    expect(findingGroup('hypothesis')).toBe('hypothesis');
    expect(findingGroup('inconclusive')).toBe('open');
    expect(findingGroup('patch_rejected')).toBe('open');
    expect(findingGroup('validated')).toBe('open');
    expect(findingGroup('verified')).toBe('fixed');
    expect(findingGroup('hardened')).toBe('fixed');
    expect(findingGroup('refuted')).toBe('closed');
    expect(findingGroup('accepted_risk')).toBe('closed');
    expect(findingGroup('closed')).toBe('closed');
  });

  it('counts findings by state for the empty-list summary', () => {
    expect(findingGroupCounts([
      { status: 'verified' },
      { status: 'hypothesis' },
      { status: 'refuted' },
      { status: 'inconclusive' },
    ])).toEqual({ hypothesis: 1, open: 1, fixed: 1, closed: 1 });
  });
});
