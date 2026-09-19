import { getGeminiApiKey, getGroqApiKey, getLLMProvider, safeGetItem, safeSetItem } from './storage';

export const getApiBaseUrl = (): string => {
  const envUrl = process.env.NEXT_PUBLIC_API_URL;
  if (envUrl && envUrl.trim()) {
    const raw = envUrl.trim();
    if (raw.startsWith('http://') || raw.startsWith('https://')) {
      return raw.replace(/\/+$/, '');
    }
    return `https://${raw.replace(/\/+$/, '')}`;
  }

  if (typeof window !== 'undefined') {
    // If the frontend is loaded through Next.js dev server (port 3000, 3001, etc.),
    // route API requests directly to the FastAPI backend on port 8080.
    if (window.location.port !== '' && window.location.port !== '8080') {
      return `${window.location.protocol}//${window.location.hostname}:8080`;
    }
    // When served via FastAPI static mount (port 8080) or behind a production reverse proxy,
    // use a relative path so all /api requests target the host.
    return '';
  }

  return 'http://localhost:8080';
};

export const API_BASE_URL = {
  toString(): string {
    return getApiBaseUrl();
  },
  valueOf(): string {
    return getApiBaseUrl();
  },
};
/**
 * Safe fetch wrapper that intercepts browser-level network errors
 * (TypeError: Failed to fetch) and translates them into clear, actionable messages.
 */
export async function safeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, { credentials: 'include', ...init });
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      throw err;
    }
    if (
      err instanceof TypeError &&
      (err.message === 'Failed to fetch' || err.message.includes('NetworkError') || err.message.includes('fetch'))
    ) {
      const target = getApiBaseUrl() || 'http://localhost:8080';
      throw new Error(
        `Unable to reach the RepoTalk backend at ${target}. Please ensure the backend server is running and accessible.`
      );
    }
    throw err;
  }
}

async function readJson<T = any>(res: Response, fallbackMessage: string): Promise<T> {
  const text = await res.text().catch(() => '');
  let payload: any = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      if (!res.ok) {
        throw new Error(`Server error HTTP ${res.status}: ${res.statusText || fallbackMessage}`);
      }
      throw new Error(fallbackMessage);
    }
  }

  if (!res.ok) {
    const errorMsg =
      payload?.detail ||
      payload?.message ||
      `Server error HTTP ${res.status}: ${res.statusText || fallbackMessage}`;
    throw new Error(typeof errorMsg === 'string' ? errorMsg : JSON.stringify(errorMsg));
  }

  return payload as T;
}

export const getSessionId = (): string => {
  if (typeof window === 'undefined') return '';
  let sid = safeGetItem('repotalks_session_id');
  if (!sid) {
    sid = typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : 'sess_' + Math.random().toString(36).substring(2) + Date.now().toString(36);
    safeSetItem('repotalks_session_id', sid);
  }
  return sid;
};

/**
 * Base headers without any provider credentials.
 * Used for non-LLM routes: project listing, selection, deletion, local search.
 */
export const getBaseHeaders = (extraHeaders: Record<string, string> = {}): Record<string, string> => {
  const sessionId = getSessionId();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...extraHeaders,
  };
  if (sessionId) {
    headers['X-Session-Id'] = sessionId;
  }
  return headers;
};

/**
 * Headers with Gemini key only.
 * Used for repository ingestion and embedding generation.
 */
export const getGeminiHeaders = (extraHeaders: Record<string, string> = {}): Record<string, string> => {
  const headers = getBaseHeaders(extraHeaders);
  const geminiKey = getGeminiApiKey();
  if (geminiKey) {
    headers['X-Gemini-API-Key'] = geminiKey;
  }
  return headers;
};

/**
 * Headers with request-scoped credentials for LLM text generation.
 * Includes Gemini key, Groq key, and LLM provider preference header.
 */
export const getGenerationHeaders = (extraHeaders: Record<string, string> = {}): Record<string, string> => {
  const headers = getBaseHeaders(extraHeaders);
  const geminiKey = getGeminiApiKey();
  const groqKey = getGroqApiKey();
  const provider = getLLMProvider();

  if (geminiKey) {
    headers['X-Gemini-API-Key'] = geminiKey;
  }
  if (groqKey) {
    headers['X-Groq-API-Key'] = groqKey;
  }
  if (provider) {
    headers['X-LLM-Provider'] = provider;
  }
  return headers;
};

export const getHeaders = getGenerationHeaders;

export interface GeminiKeyTestResult {
  success: boolean;
  message: string;
  model?: string;
  reply?: string;
}

export interface GroqKeyTestResult {
  success: boolean;
  message: string;
  model?: string;
  reply?: string;
}

export async function testGeminiKey(apiKey: string, signal?: AbortSignal): Promise<GeminiKeyTestResult> {
  const res = await safeFetch(`${getApiBaseUrl()}/api/settings/test`, {
    method: 'POST',
    headers: getBaseHeaders(),
    body: JSON.stringify({ api_key: apiKey }),
    signal,
  });
  return readJson<GeminiKeyTestResult>(res, 'Gemini connection test failed');
}

export async function testGroqKey(apiKey: string, signal?: AbortSignal): Promise<GroqKeyTestResult> {
  const res = await safeFetch(`${getApiBaseUrl()}/api/settings/test-groq`, {
    method: 'POST',
    headers: getBaseHeaders(),
    body: JSON.stringify({ api_key: apiKey }),
    signal,
  });
  return readJson<GroqKeyTestResult>(res, 'Groq connection test failed');
}

export async function fetchProjects(signal?: AbortSignal) {
  // Never send provider keys to project listing
  const res = await safeFetch(`${getApiBaseUrl()}/api/projects`, {
    headers: getBaseHeaders(),
    signal,
  });
  return readJson<{ projects: any[]; active_project: any }>(res, 'Failed to fetch projects list');
}

export async function selectProject(repoId: string, signal?: AbortSignal) {
  // Never send provider keys to project selection
  const res = await safeFetch(`${getApiBaseUrl()}/api/projects/select`, {
    method: 'POST',
    headers: getBaseHeaders(),
    body: JSON.stringify({ repo_id: repoId }),
    signal,
  });
  return readJson(res, 'Failed to select active project');
}

export async function deleteProject(repoId: string, signal?: AbortSignal) {
  // Never send provider keys to project deletion
  const res = await safeFetch(`${getApiBaseUrl()}/api/projects/${repoId}`, {
    method: 'DELETE',
    headers: getBaseHeaders(),
    signal,
  });
  return readJson(res, 'Failed to delete project');
}

export async function quickSearch(repoId: string, query: string, signal?: AbortSignal) {
  // Never send provider keys to local search
  const res = await safeFetch(`${getApiBaseUrl()}/api/search/quick`, {
    method: 'POST',
    headers: getBaseHeaders(),
    body: JSON.stringify({ repo_id: repoId, query }),
    signal,
  });
  return readJson(res, 'Quick search failed');
}

export async function ingestGitHubRepo(repoUrl: string, signal?: AbortSignal) {
  const res = await safeFetch(`${getApiBaseUrl()}/api/ingest/github`, {
    method: 'POST',
    headers: getGeminiHeaders(),
    body: JSON.stringify({ repo_url: repoUrl }),
    signal,
  });
  return readJson(res, 'GitHub ingestion failed');
}

export async function ingestUploadZip(file: File, signal?: AbortSignal) {
  const geminiKey = getGeminiApiKey();
  const sessionId = getSessionId();
  const formData = new FormData();
  formData.append('file', file);

  const headers: Record<string, string> = {};
  if (geminiKey) {
    headers['X-Gemini-API-Key'] = geminiKey;
  }
  if (sessionId) {
    headers['X-Session-Id'] = sessionId;
  }

  const res = await safeFetch(`${getApiBaseUrl()}/api/ingest/upload`, {
    method: 'POST',
    headers,
    body: formData,
    signal,
  });
  return readJson(res, 'Zip upload failed');
}

export type ChatHistoryItem = { sender: 'user' | 'assistant'; text: string };

export interface ChatStreamMeta {
  provider: string;
  model: string;
  fallback_used: boolean;
}

export type StreamChatOutcome =
  | { type: 'completed'; contentReceived: boolean }
  | { type: 'interrupted'; reason: string }
  | { type: 'aborted' };

export async function streamChat(
  repoId: string,
  message: string,
  onChunk: (text: string) => void,
  onCitations?: (citations: any[]) => void,
  contextFile?: string,
  history?: ChatHistoryItem[],
  signal?: AbortSignal,
  onMeta?: (meta: ChatStreamMeta) => void,
): Promise<StreamChatOutcome> {
  const res = await safeFetch(`${getApiBaseUrl()}/api/chat/stream`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({ repo_id: repoId, message, context_file: contextFile, history }),
    signal,
  });

  if (!res.ok || !res.body) {
    let errorDetail = `Failed to connect to chat stream (HTTP ${res.status})`;
    try {
      const text = await res.text();
      const err = JSON.parse(text);
      if (err && (err.detail || err.message)) {
        errorDetail = err.detail || err.message;
      }
    } catch {
      // pass
    }
    throw new Error(errorDetail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let contentReceived = false;
  let doneReceived = false;

  const delimiterRegex = /\r\n\r\n|\n\n|\r\r/;

  const processFrame = (rawFrame: string) => {
    const lines = rawFrame.split(/\r?\n/);
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || line.startsWith(':')) continue;
      if (line.startsWith('data:')) {
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') {
          doneReceived = true;
          return;
        }
        let parsed: any;
        try {
          parsed = JSON.parse(payload);
        } catch {
          continue;
        }

        if (parsed.type === 'meta') {
          if (onMeta) {
            onMeta({
              provider: parsed.provider,
              model: parsed.model,
              fallback_used: parsed.fallback_used,
            });
          }
          continue;
        }

        if (parsed.citations && onCitations) {
          onCitations(parsed.citations);
        }
        if (parsed.text) {
          if (typeof parsed.text === 'string' && parsed.text.trim().length > 0) {
            contentReceived = true;
          }
          onChunk(parsed.text);
        }
        if (parsed.error) {
          throw new Error(parsed.error);
        }
      }
    }
  };

  try {
    while (true) {
      if (signal?.aborted) {
        return { type: 'aborted' };
      }

      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let match: RegExpExecArray | null;
      while ((match = delimiterRegex.exec(buffer)) !== null) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        if (frame.trim()) {
          processFrame(frame);
          if (doneReceived) {
            return { type: 'completed', contentReceived };
          }
        }
      }
    }

    // Flush any remaining decoder bytes
    buffer += decoder.decode();
    if (buffer.trim()) {
      processFrame(buffer);
      buffer = '';
    }

    if (signal?.aborted) {
      return { type: 'aborted' };
    }

    if (doneReceived) {
      return { type: 'completed', contentReceived };
    }

    return {
      type: 'interrupted',
      reason: 'Stream closed before completion signal ([DONE]) was received.',
    };
  } catch (err: any) {
    if (err?.name === 'AbortError' || signal?.aborted) {
      return { type: 'aborted' };
    }
    throw err;
  } finally {
    try { reader.cancel().catch(() => {}); } catch { /* noop */ }
    try { reader.releaseLock(); } catch { /* noop */ }
  }
}

export async function chatMultimodal(repoId: string, message: string, image: File, signal?: AbortSignal) {
  const formData = new FormData();
  formData.append('repo_id', repoId);
  formData.append('message', message);
  formData.append('image', image);

  const headers: Record<string, string> = {};
  const geminiKey = getGeminiApiKey();
  const groqKey = getGroqApiKey();
  const provider = getLLMProvider();
  const sessionId = getSessionId();

  if (geminiKey) headers['X-Gemini-API-Key'] = geminiKey;
  if (groqKey) headers['X-Groq-API-Key'] = groqKey;
  if (provider) headers['X-LLM-Provider'] = provider;
  if (sessionId) headers['X-Session-Id'] = sessionId;

  const res = await safeFetch(`${getApiBaseUrl()}/api/chat/multimodal`, {
    method: 'POST',
    headers,
    body: formData,
    signal,
  });
  return readJson<{ text: string; citations: any[] }>(res, 'Multimodal chat failed');
}

export async function fetchVivaQuestion(
  repoId: string,
  topic?: string,
  difficulty: string = 'medium',
  previousHistory?: any[],
  signal?: AbortSignal
) {
  const res = await safeFetch(`${getApiBaseUrl()}/api/viva/question`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({
      repo_id: repoId,
      topic,
      difficulty,
      previous_history: previousHistory,
    }),
    signal,
  });
  return readJson(res, 'Failed to generate viva question');
}

export async function evaluateVivaAnswer(
  repoId: string,
  question: string,
  studentAnswer: string,
  contextFile?: string,
  signal?: AbortSignal
) {
  const res = await safeFetch(`${getApiBaseUrl()}/api/viva/evaluate`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({
      repo_id: repoId,
      question,
      student_answer: studentAnswer,
      context_file: contextFile,
    }),
    signal,
  });
  return readJson(res, 'Failed to evaluate viva answer');
}

export async function fetchVivaSummary(repoId: string, sessionHistory: any[], signal?: AbortSignal) {
  const res = await safeFetch(`${getApiBaseUrl()}/api/viva/summary`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({
      repo_id: repoId,
      session_history: sessionHistory,
    }),
    signal,
  });
  return readJson(res, 'Failed to generate viva summary');
}

export async function fetchVivaQuestionBank(repoId: string, signal?: AbortSignal) {
  const res = await safeFetch(`${getApiBaseUrl()}/api/viva/question-bank`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({
      repo_id: repoId,
    }),
    signal,
  });
  return readJson(res, 'Failed to fetch question bank');
}

export type DiagramType = 'component_tree' | 'api_flow' | 'data_flow' | 'db_schema';

export interface ArchitectureNode {
  id: string;
  label: string;
  type: string;
  file?: string;
  dir?: string;
  description?: string;
  connected_to?: string[];
  data_passed?: string[];
  is_group?: boolean;
}

export interface ArchitectureEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
}

export interface ArchitectureResponse {
  diagram_type: DiagramType;
  has_data: boolean;
  reason?: string;
  nodes: Record<string, ArchitectureNode>;
  edges: ArchitectureEdge[];
  mind_map?: any;
}

export async function fetchArchitectureMap(
  repoId: string,
  diagramType: DiagramType = 'component_tree',
  forceRefresh: boolean = false,
  signal?: AbortSignal
): Promise<ArchitectureResponse> {
  const res = await safeFetch(`${getApiBaseUrl()}/api/architecture`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({
      repo_id: repoId,
      diagram_type: diagramType,
      force_refresh: forceRefresh,
    }),
    signal,
  });
  return readJson<ArchitectureResponse>(res, 'Failed to generate architecture map');
}

export async function fetchCodeTrace(repoId: string, flowQuery: string, signal?: AbortSignal) {
  const res = await safeFetch(`${getApiBaseUrl()}/api/tracer`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({
      repo_id: repoId,
      flow_query: flowQuery,
    }),
    signal,
  });
  return readJson(res, 'Failed to generate code trace');
}

export async function streamTracerNarration(
  repoId: string,
  flowQuery: string,
  hopInfo: any,
  onChunk: (text: string) => void,
  signal?: AbortSignal
) {
  const res = await safeFetch(`${getApiBaseUrl()}/api/tracer/stream`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({
      repo_id: repoId,
      flow_query: flowQuery,
      hop_info: hopInfo,
    }),
    signal,
  });

  if (!res.ok || !res.body) {
    let errorDetail = `Failed to stream tracer narration (HTTP ${res.status})`;
    try {
      const text = await res.text();
      const err = JSON.parse(text);
      if (err && (err.detail || err.message)) {
        errorDetail = err.detail || err.message;
      }
    } catch {
      // pass
    }
    throw new Error(errorDetail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const payload = line.slice(6).trim();
          if (payload === '[DONE]') return;
          try {
            const parsed = JSON.parse(payload);
            if (parsed.text) {
              onChunk(parsed.text);
            }
          } catch {
            // pass
          }
        }
      }
    }
  } catch (err: any) {
    if (err?.name === 'AbortError' || signal?.aborted) {
      return;
    }
    throw err;
  } finally {
    try { reader.cancel().catch(() => {}); } catch { /* noop */ }
    try { reader.releaseLock(); } catch { /* noop */ }
  }
}

export async function fetchAudienceExplanation(
  repoId: string,
  audience: string,
  filePath?: string,
  signal?: AbortSignal
) {
  const res = await safeFetch(`${getApiBaseUrl()}/api/audience`, {
    method: 'POST',
    headers: getGenerationHeaders(),
    body: JSON.stringify({
      repo_id: repoId,
      audience,
      file_path: filePath,
    }),
    signal,
  });
  return readJson(res, 'Failed to fetch audience explanation');
}

export interface IngestionJob {
  job_id: string;
  source_type: 'github' | 'zip';
  repo_name: string;
  status: 'pending' | 'cloning' | 'extracting' | 'parsing' | 'embedding' | 'indexing' | 'completed' | 'failed' | 'cancelled';
  progress_percent: number;
  stage: string;
  error_message?: string | null;
  total_chunks: number;
  embedded_chunks: number;
}

export async function fetchJobs(signal?: AbortSignal): Promise<{ jobs: IngestionJob[] }> {
  const res = await safeFetch(`${getApiBaseUrl()}/api/jobs`, {
    headers: getGeminiHeaders(),
    signal,
  });
  return readJson(res, 'Failed to fetch ingestion jobs');
}

export async function fetchJob(jobId: string, signal?: AbortSignal): Promise<IngestionJob> {
  const res = await safeFetch(`${getApiBaseUrl()}/api/jobs/${jobId}`, {
    headers: getGeminiHeaders(),
    signal,
  });
  return readJson(res, 'Failed to fetch job');
}

export async function cancelJob(jobId: string, signal?: AbortSignal): Promise<{ cancelled: boolean; status: string }> {
  const res = await safeFetch(`${getApiBaseUrl()}/api/jobs/${jobId}/cancel`, {
    method: 'POST',
    headers: getGeminiHeaders(),
    signal,
  });
  return readJson(res, 'Failed to cancel job');
}

export async function subscribeToJobProgress(
  jobId: string,
  onProgress: (job: IngestionJob) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await safeFetch(`${getApiBaseUrl()}/api/jobs/${jobId}/progress`, {
    headers: getGeminiHeaders(),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`Failed to subscribe to job progress (HTTP ${res.status})`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const payload = line.slice(6).trim();
          if (payload === '[DONE]') return;
          try {
            const parsed = JSON.parse(payload);
            onProgress(parsed);
          } catch {
            // pass
          }
        }
      }
    }
  } catch (err: any) {
    if (err?.name === 'AbortError' || signal?.aborted) {
      return;
    }
    throw err;
  } finally {
    try { reader.cancel().catch(() => {}); } catch { /* noop */ }
    try { reader.releaseLock(); } catch { /* noop */ }
  }
}

