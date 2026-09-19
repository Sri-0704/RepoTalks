import { useState, useRef, useEffect, useCallback } from 'react';

export type RequestStatus = 'idle' | 'loading' | 'success' | 'error';

export interface UseAbortableRequestOptions<T, P extends any[]> {
  repoId?: string;
  fn: (signal: AbortSignal, ...args: P) => Promise<T>;
  onSuccess?: (data: T) => void;
  onError?: (err: Error) => void;
}

export interface UseAbortableRequestReturn<T, P extends any[]> {
  status: RequestStatus;
  data: T | null;
  error: string | null;
  loading: boolean;
  execute: (...args: P) => Promise<T | null>;
  retry: () => Promise<T | null>;
  reset: () => void;
  abort: () => void;
}

export function useAbortableRequest<T = any, P extends any[] = any[]>({
  repoId,
  fn,
  onSuccess,
  onError,
}: UseAbortableRequestOptions<T, P>): UseAbortableRequestReturn<T, P> {
  const [status, setStatus] = useState<RequestStatus>('idle');
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const currentRepoRef = useRef<string | undefined>(repoId);
  const lastArgsRef = useRef<P | null>(null);

  currentRepoRef.current = repoId;

  // Abort in-flight request if repoId changes
  useEffect(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setStatus('idle');
    setData(null);
    setError(null);
    lastArgsRef.current = null;
  }, [repoId]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
      }
    };
  }, []);

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setStatus('idle');
  }, []);

  const reset = useCallback(() => {
    abort();
    setData(null);
    setError(null);
    setStatus('idle');
  }, [abort]);

  const execute = useCallback(
    async (...args: P): Promise<T | null> => {
      lastArgsRef.current = args;

      // Abort prior request
      if (abortRef.current) {
        abortRef.current.abort();
      }

      const controller = new AbortController();
      abortRef.current = controller;
      const requestRepoId = currentRepoRef.current;

      setStatus('loading');
      setError(null);

      try {
        const result = await fn(controller.signal, ...args);

        // Discard if repoId changed during the in-flight request
        if (requestRepoId !== currentRepoRef.current) {
          return null;
        }

        setData(result);
        setStatus('success');
        onSuccess?.(result);
        return result;
      } catch (err: any) {
        if (err.name === 'AbortError') {
          return null;
        }

        // Discard if repoId changed
        if (requestRepoId !== currentRepoRef.current) {
          return null;
        }

        const msg = err.message || 'Request failed';
        setError(msg);
        setStatus('error');
        onError?.(err instanceof Error ? err : new Error(msg));
        return null;
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      }
    },
    [fn, onSuccess, onError]
  );

  const retry = useCallback(async (): Promise<T | null> => {
    if (lastArgsRef.current) {
      return execute(...lastArgsRef.current);
    }
    return null;
  }, [execute]);

  return {
    status,
    data,
    error,
    loading: status === 'loading',
    execute,
    retry,
    reset,
    abort,
  };
}
