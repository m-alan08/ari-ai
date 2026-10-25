import { describe, expect, it } from 'vitest';
import { relativeTime } from '@/lib/format';

describe('relativeTime', () => {
  const now = Date.parse('2026-01-01T00:00:00.000Z');

  it('keeps past timestamps in ago form', () => {
    expect(relativeTime(new Date(now - 30_000).toISOString(), now)).toBe('30s ago');
  });

  it('describes near-future timestamps as upcoming', () => {
    expect(relativeTime(new Date(now + 30_000).toISOString(), now)).toBe('in 30 seconds');
  });

  it('keeps sub-second future timestamps in future wording', () => {
    expect(relativeTime(new Date(now + 250).toISOString(), now)).toBe('in 1 second');
  });

  it('describes a seven-day future timestamp in days', () => {
    expect(relativeTime(new Date(now + 7 * 86_400_000).toISOString(), now)).toBe('in 7 days');
  });
});
