'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export type Resource<T> =
  | { status: 'loading'; data: undefined; error: undefined; reload: () => void; refreshError?: undefined }
  /** `refreshError` is set when a background refresh failed; `data` is then the last good copy (stale). */
  | { status: 'ready'; data: T; error: undefined; reload: () => void; refreshError?: unknown }
  | { status: 'error'; data: undefined; error: unknown; reload: () => void; refreshError?: undefined };

interface State<T> { key: string | null; status: 'loading' | 'ready' | 'error'; data?: T; error?: unknown; refreshError?: unknown }

/**
 * Fetch on mount and whenever `key` changes. Earlier requests are aborted so a
 * slow stale response can never overwrite a newer one. `pollMs` refreshes in
 * place: the last good data stays visible while it reloads, and if a refresh
 * fails the data is kept, `refreshError` is exposed and polling continues.
 * `shouldPoll` may return false to stop polling once the data settles (e.g. a
 * terminal state); a failing refresh keeps polling regardless.
 * Data produced for a previous key is never returned for a new key.
 */
export function useResource<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>, pollMs?: number, shouldPoll?: (data: T) => boolean): Resource<T> {
  const [state, setState] = useState<State<T>>({ key, status: 'loading' });
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  const shouldPollRef = useRef(shouldPoll);
  useEffect(() => { loadRef.current = load; });
  useEffect(() => { shouldPollRef.current = shouldPoll; });
  const reload = useCallback(() => setNonce(n => n + 1), []);

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let hadData = false;
    const schedule = (data?: T) => { if (pollMs && (data === undefined || (shouldPollRef.current?.(data) ?? true))) timer = setTimeout(() => run(false), pollMs); };
    const run = (first: boolean) => {
      if (first) setState(s => (s.key === key && s.status === 'ready' ? s : { key, status: 'loading' }));
      loadRef.current(controller.signal).then(
        data => {
          if (controller.signal.aborted) return;
          hadData = true;
          setState({ key, status: 'ready', data });
          schedule(data);
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          // Keep the last good data for this key; only a load with nothing to show becomes an error.
          setState(s => (s.key === key && s.status === 'ready' ? { ...s, refreshError: error } : { key, status: 'error', error }));
          if (hadData || !first) schedule();
        },
      );
    };
    run(true);
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [key, nonce, pollMs]);

  if (state.key !== key) return { status: 'loading', data: undefined, error: undefined, reload };
  if (state.status === 'ready') return { status: 'ready', data: state.data as T, error: undefined, reload, refreshError: state.refreshError };
  if (state.status === 'error') return { status: 'error', data: undefined, error: state.error, reload };
  return { status: 'loading', data: undefined, error: undefined, reload };
}
