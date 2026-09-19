export interface StoredRepo {
  repo_id: string;
  name: string;
  source_type?: string;
  url_or_name?: string;
  created_at?: string;
  file_count: number;
  total_lines: number;
  stack?: string[];
  tree: any[];
  files: any[];
  is_active?: boolean;
}

export type LLMProviderPreference = 'auto' | 'groq' | 'gemini';

// Note: Browser localStorage is convenience storage only and must not be
// described or treated as secure credential storage.
export const STORAGE_KEYS = {
  GEMINI_API_KEY: 'repotalks_gemini_api_key',
  GROQ_API_KEY: 'repotalks_groq_api_key',
  LLM_PROVIDER: 'repotalks_llm_provider',
  ACTIVE_REPO: 'repotalks_active_repo',
};

// In-memory fallback for private browsing mode or storage-restricted environments
const memoryStorage: Record<string, string> = {};

export const safeGetItem = (key: string): string | null => {
  if (typeof window === 'undefined') return null;
  try {
    return localStorage.getItem(key);
  } catch {
    return memoryStorage[key] ?? null;
  }
};

export const safeSetItem = (key: string, value: string): void => {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(key, value);
  } catch {
    memoryStorage[key] = value;
  }
};

export const safeRemoveItem = (key: string): void => {
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(key);
  } catch {
    delete memoryStorage[key];
  }
};

export const getGeminiApiKey = (): string => {
  return safeGetItem(STORAGE_KEYS.GEMINI_API_KEY) || '';
};

export const setGeminiApiKey = (key: string): void => {
  safeSetItem(STORAGE_KEYS.GEMINI_API_KEY, key.trim());
};

export const removeGeminiApiKey = (): void => {
  safeRemoveItem(STORAGE_KEYS.GEMINI_API_KEY);
};

export const getGroqApiKey = (): string => {
  return safeGetItem(STORAGE_KEYS.GROQ_API_KEY) || '';
};

export const setGroqApiKey = (key: string): void => {
  safeSetItem(STORAGE_KEYS.GROQ_API_KEY, key.trim());
};

export const removeGroqApiKey = (): void => {
  safeRemoveItem(STORAGE_KEYS.GROQ_API_KEY);
};

export const getLLMProvider = (): LLMProviderPreference => {
  const val = (safeGetItem(STORAGE_KEYS.LLM_PROVIDER) || 'auto').toLowerCase();
  if (val === 'groq' || val === 'gemini') return val;
  return 'auto';
};

export const setLLMProvider = (provider: LLMProviderPreference): void => {
  safeSetItem(STORAGE_KEYS.LLM_PROVIDER, provider);
};

export const getActiveRepo = (): StoredRepo | null => {
  const raw = safeGetItem(STORAGE_KEYS.ACTIVE_REPO);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

export const setActiveRepo = (repo: StoredRepo | null): void => {
  if (!repo) {
    safeRemoveItem(STORAGE_KEYS.ACTIVE_REPO);
  } else {
    // Only persist compact repository metadata — never store large trees or files in localStorage
    const { tree, files, ...compact } = repo;
    safeSetItem(STORAGE_KEYS.ACTIVE_REPO, JSON.stringify(compact));
  }
};
