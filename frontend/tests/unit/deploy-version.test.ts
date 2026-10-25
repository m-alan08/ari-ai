import { describe, expect, it } from 'vitest';
import { deployPair } from '@/lib/deploy-version';

const FE = { gitSha: 'aaa', schemaVersion: '0.22.0' };

describe('frontend/backend deploy pairing', () => {
  it('is compatible on equal schema versions regardless of commit', () => {
    const pair = deployPair(FE, { gitSha: 'bbb', schemaVersion: '0.22.0' }, true);
    expect(pair.status).toBe('compatible');
    expect(pair.sameCommit).toBe(false); // additive bumps deploy backend-first
  });

  it('flags schema mismatch even when commits match', () => {
    const pair = deployPair(FE, { gitSha: 'aaa', schemaVersion: '0.21.0' }, true);
    expect(pair.status).toBe('mismatch');
    expect(pair.sameCommit).toBe(true);
  });

  it('reports unknown when the backend is unreachable or predates the field', () => {
    expect(deployPair(FE, null, false).status).toBe('unknown');
    expect(deployPair(FE, null, true).status).toBe('unknown');
    expect(deployPair(FE, { gitSha: 'x', schemaVersion: null }, true).status).toBe('unknown');
  });
});
