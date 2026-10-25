import { describe, expect, it } from 'vitest';
import { REQUEST_SCOPE_NOTE, SCOPE_SANDBOX_EXPLANATION } from '@/lib/scope';

describe('request scope note', () => {
  it('names pending authorization and does not claim activation, a check, or open access', () => {
    expect(REQUEST_SCOPE_NOTE).toContain('pending authorization');
    expect(REQUEST_SCOPE_NOTE).toContain('project owner explicitly approves');
    expect(REQUEST_SCOPE_NOTE).toContain('owners may approve their own sandbox-only scopes');
    expect(REQUEST_SCOPE_NOTE).toContain('does not start a check');
    expect(REQUEST_SCOPE_NOTE).toContain('outside those targets');
  });

  it('explains the sandbox and build recipe in plain language', () => {
    expect(SCOPE_SANDBOX_EXPLANATION).toContain('isolated copy of your app');
    expect(SCOPE_SANDBOX_EXPLANATION).toContain('nothing touches your live site');
    expect(SCOPE_SANDBOX_EXPLANATION).toContain('build recipe');
  });
});
