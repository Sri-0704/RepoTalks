'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Search, FileCode, X, Loader2, Hash } from 'lucide-react';
import { quickSearch } from '@/lib/api';
import { cn } from '@/lib/utils';

interface QuickSearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  activeRepoId: string | null;
  onSelectResult?: (result: SearchResult) => void;
}

interface SearchResult {
  type: 'file' | 'symbol' | 'chunk';
  path: string;
  snippet?: string;
  name?: string;
  score?: number;
}

export const QuickSearchModal: React.FC<QuickSearchModalProps> = ({ isOpen, onClose, activeRepoId, onSelectResult }) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Focus input when opened
  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setResults([]);
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  // Search with debounce
  useEffect(() => {
    if (!isOpen || !activeRepoId || !query.trim()) {
      setResults([]);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await quickSearch(activeRepoId, query.trim(), controller.signal);
        let normalized: SearchResult[] = [];
        if (Array.isArray(data.results)) {
          normalized = data.results.map((r: any) => ({
            type: r.type || 'file',
            path: r.file_path || r.path || r.name || '',
            snippet: r.snippet || r.text || '',
            name: r.name || r.symbol || '',
            score: r.similarity,
          }));
        } else {
          const fileResults: SearchResult[] = (data.files || []).map((f: any) => ({
            type: 'file' as const,
            path: f.path || '',
            name: f.path?.split('/').pop() || f.path || '',
            snippet: f.extension ? `${f.extension.toUpperCase()} file (${f.line_count || 0} lines)` : undefined,
          }));
          const symbolResults: SearchResult[] = (data.symbols || []).map((s: any) => ({
            type: 'symbol' as const,
            path: s.file_path || '',
            name: s.name || '',
            snippet: `${s.type || 'symbol'} in ${s.file_path || ''}:${s.line || 1}`,
          }));
          const chunkResults: SearchResult[] = (data.chunks || []).map((c: any) => ({
            type: 'chunk' as const,
            path: c.file_path || '',
            snippet: c.snippet || '',
            score: c.similarity,
          }));
          normalized = [...fileResults, ...symbolResults, ...chunkResults];
        }
        setResults(normalized);
        setSelectedIndex(0);
      } catch (err: any) {
        if (err.name !== 'AbortError') {
          setResults([]);
        }
      } finally {
        setLoading(false);
      }
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, activeRepoId, isOpen]);

  const handleSelectResult = useCallback((result: SearchResult) => {
    if (onSelectResult) {
      onSelectResult(result);
    } else if (result.path) {
      window.dispatchEvent(new CustomEvent('repotalks:select-file', { detail: { path: result.path } }));
    }
    onClose();
  }, [onClose, onSelectResult]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose();
    } else if (e.key === 'Tab') {
      if (!modalRef.current) return;
      const focusable = modalRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length > 0) {
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev) => Math.min(prev + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter' && results[selectedIndex]) {
      e.preventDefault();
      handleSelectResult(results[selectedIndex]);
    }
  }, [onClose, results, selectedIndex, handleSelectResult]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-label="Search files and symbols"
    >
      <div className="fixed inset-0 bg-foreground/20" aria-hidden="true" />

      <div ref={modalRef} className="relative z-10 w-full max-w-lg bg-surface border border-border rounded-xl shadow-2xl overflow-hidden animate-slide-down">
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <Search className="w-4 h-4 text-muted-foreground shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={activeRepoId ? 'Search files, symbols, code…' : 'Add a repository to search'}
            disabled={!activeRepoId}
            className="flex-1 bg-transparent text-body text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-50"
          />
          {loading && <Loader2 className="w-4 h-4 animate-spin text-primary" />}
          <button
            onClick={onClose}
            className="p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Close search"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Results */}
        {results.length > 0 && (
          <div className="max-h-[320px] overflow-y-auto py-1">
            {results.map((result, idx) => (
              <button
                key={idx}
                className={cn(
                  'w-full flex items-start gap-3 px-4 py-2.5 text-left transition-colors',
                  selectedIndex === idx ? 'bg-selected' : 'hover:bg-muted-surface'
                )}
                onClick={() => handleSelectResult(result)}
                onMouseEnter={() => setSelectedIndex(idx)}
              >
                {result.type === 'symbol' ? (
                  <Hash className="w-3.5 h-3.5 text-primary mt-1 shrink-0" />
                ) : (
                  <FileCode className="w-3.5 h-3.5 text-muted-foreground mt-1 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-label font-medium text-foreground truncate">
                    {result.name || result.path?.split('/').pop()}
                  </p>
                  <p className="text-meta text-muted-foreground font-mono truncate">
                    {result.path}
                  </p>
                  {result.snippet && (
                    <p className="text-meta text-muted-foreground truncate mt-0.5">{result.snippet}</p>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}

        {/* Empty state */}
        {query.trim() && !loading && results.length === 0 && (
          <div className="py-8 text-center text-meta text-muted-foreground">
            No results for "{query}"
          </div>
        )}

        {/* Footer */}
        <div className="px-4 py-2 border-t border-border bg-muted-surface flex items-center justify-between text-[11px] text-muted-foreground">
          <div className="flex items-center gap-3">
            <span><kbd className="px-1 py-0.5 rounded bg-surface border border-border font-mono">↑↓</kbd> Navigate</span>
            <span><kbd className="px-1 py-0.5 rounded bg-surface border border-border font-mono">↵</kbd> Open</span>
          </div>
          <span><kbd className="px-1 py-0.5 rounded bg-surface border border-border font-mono">Esc</kbd> Close</span>
        </div>
      </div>
    </div>
  );
};
