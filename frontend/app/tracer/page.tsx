'use client';

import React, { useState, useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import {
  Compass,
  Play,
  Code,
  Sparkles,
  Loader2,
  FileCode,
  ChevronRight,
  AlertCircle,
  RotateCcw,
} from 'lucide-react';
import { StreamingText } from '@/components/StreamingText';
import { fetchCodeTrace, streamTracerNarration } from '@/lib/api';
import { useProject } from '@/context/ProjectContext';
import { cn } from '@/lib/utils';

export default function TracerPage() {
  const { activeRepo } = useProject();
  const [flowQuery, setFlowQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [traceData, setTraceData] = useState<any | null>(null);
  const [selectedHopIdx, setSelectedHopIdx] = useState<number>(0);
  const [isStreamingNarration, setIsStreamingNarration] = useState(false);
  const [expandedNarrations, setExpandedNarrations] = useState<Record<number, string>>({});
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortControllerRef.current?.abort();
    setFlowQuery('');
    setTraceData(null);
    setSelectedHopIdx(0);
    setExpandedNarrations({});
    setError(null);
  }, [activeRepo?.repo_id]);

  useEffect(() => {
    return () => { abortControllerRef.current?.abort(); };
  }, []);

  const handleTraceSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!flowQuery.trim() || !activeRepo) return;

    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    setTraceData(null);
    setExpandedNarrations({});
    setSelectedHopIdx(0);
    setError(null);

    try {
      const data = await fetchCodeTrace(activeRepo.repo_id, flowQuery.trim(), controller.signal);
      setTraceData(data);
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setError(err.message || 'Trace failed');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleExpandNarration = async (hopIdx: number) => {
    if (!activeRepo || !traceData || !traceData.hops[hopIdx]) return;
    if (expandedNarrations[hopIdx]) return;

    const hop = traceData.hops[hopIdx];
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setIsStreamingNarration(true);
    try {
      let accumulated = '';
      await streamTracerNarration(activeRepo.repo_id, flowQuery.trim(), hop, (chunk) => {
        accumulated += chunk;
        setExpandedNarrations((prev) => ({ ...prev, [hopIdx]: accumulated }));
      }, controller.signal);
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setExpandedNarrations((prev) => ({
          ...prev,
          [hopIdx]: hop.narration || 'Narration unavailable.',
        }));
      }
    } finally {
      setIsStreamingNarration(false);
    }
  };

  const handleHopClick = (idx: number) => {
    if (isStreamingNarration) {
      abortControllerRef.current?.abort();
      setIsStreamingNarration(false);
    }
    setSelectedHopIdx(idx);
  };

  if (!activeRepo) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] text-center">
        <Compass className="w-10 h-10 text-muted-foreground/30 mb-3" />
        <h3 className="text-label font-semibold text-foreground">No repository selected</h3>
        <p className="text-meta text-muted-foreground mt-1">Add or select a repository first.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-surface border border-border rounded-xl p-4 space-y-3">
        <div>
          <h2 className="text-section text-foreground flex items-center gap-2">
            <Compass className="w-5 h-5 text-primary" />
            Trace a flow
          </h2>
          <p className="text-meta text-muted-foreground mt-0.5">
            Explore an AI-assisted path through the repository.
          </p>
        </div>

        <form onSubmit={handleTraceSubmit} className="flex items-center gap-2">
          <input
            type="text"
            placeholder="e.g. 'What happens when a user logs in?'"
            value={flowQuery}
            onChange={(e) => setFlowQuery(e.target.value)}
            className="flex-1 px-3 py-2.5 rounded-md bg-muted-surface border border-border text-body text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <button
            type="submit"
            disabled={loading || !flowQuery.trim()}
            className="px-4 py-2.5 rounded-md bg-primary text-primary-foreground font-medium text-label hover:opacity-90 disabled:opacity-50 flex items-center gap-2 shrink-0 transition-opacity"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Trace
          </button>
        </form>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 p-4 rounded-xl bg-danger/5 border border-danger/20 text-meta text-danger">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <div className="flex-1">{error}</div>
          <button onClick={() => setError(null)} className="text-danger hover:opacity-70">
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex flex-col items-center justify-center py-20 bg-surface border border-border rounded-xl">
          <Loader2 className="w-8 h-8 animate-spin text-primary mb-3" />
          <h4 className="text-label font-semibold text-foreground">Tracing execution path…</h4>
          <p className="text-meta text-muted-foreground">Analyzing call chain and dependencies</p>
        </div>
      )}

      {/* No results */}
      {traceData && (traceData.relevant === false || !traceData.hops || traceData.hops.length === 0) && (
        <div className="flex flex-col items-center justify-center py-16 bg-surface border border-border rounded-xl text-center">
          <Compass className="w-8 h-8 text-warning mb-3" />
          <h3 className="text-label font-semibold text-foreground">No matching flow found</h3>
          <p className="text-meta text-muted-foreground max-w-md mt-1">
            {traceData.message || "Try asking about a specific feature or action, like 'what happens when the form is submitted?'"}
          </p>
        </div>
      )}

      {/* Results */}
      {traceData && traceData.hops && traceData.hops.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          {/* Steps list */}
          <div className="lg:col-span-4 bg-surface border border-border rounded-xl p-4 space-y-3">
            <div className="pb-3 border-b border-border">
              <span className="text-meta font-medium text-primary uppercase tracking-wider">Execution path</span>
              <h3 className="text-label font-semibold text-foreground mt-0.5">
                {traceData.total_hops || traceData.hops.length} steps traced
              </h3>
            </div>

            <div className="space-y-1.5">
              {traceData.hops.map((hop: any, idx: number) => {
                const isSelected = selectedHopIdx === idx;
                return (
                  <button
                    key={idx}
                    onClick={() => handleHopClick(idx)}
                    aria-selected={isSelected}
                    className={cn(
                      'w-full p-3 rounded-lg border text-left transition-colors',
                      isSelected
                        ? 'bg-selected border-primary/30 text-foreground'
                        : 'bg-surface border-border hover:bg-muted-surface text-foreground'
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className={cn('text-[11px] font-medium uppercase tracking-wider', isSelected ? 'text-primary' : 'text-muted-foreground')}>
                        Step {hop.hop_number || idx + 1}
                      </span>
                      <ChevronRight className={cn('w-3.5 h-3.5', isSelected ? 'text-primary' : 'text-muted-foreground')} />
                    </div>
                    <h4 className="text-label font-semibold truncate mt-0.5">{hop.title || hop.function_name}</h4>
                    <span className="text-meta font-mono text-muted-foreground truncate block mt-0.5">{hop.file_path}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Step detail */}
          <div className="lg:col-span-8 bg-surface border border-border rounded-xl p-5 space-y-5">
            {traceData.hops[selectedHopIdx] && (
              <>
                <div className="pb-4 border-b border-border space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded bg-selected text-selected-foreground text-meta font-medium">
                      Step {selectedHopIdx + 1} of {traceData.hops.length}
                    </span>
                    <span className="text-meta font-mono text-muted-foreground">{traceData.hops[selectedHopIdx].file_path}</span>
                  </div>
                  <h3 className="text-title text-foreground">
                    {traceData.hops[selectedHopIdx].title}
                    <span className="text-muted-foreground font-mono text-body ml-2">
                      {traceData.hops[selectedHopIdx].function_name}
                    </span>
                  </h3>
                </div>

                {/* Code snippet */}
                <div className="space-y-2">
                  <div className="text-label font-medium text-foreground flex items-center gap-1.5">
                    <Code className="w-4 h-4 text-primary" />
                    Code snippet
                  </div>
                  <pre className="p-4 rounded-lg bg-muted-surface border border-border text-code font-mono overflow-x-auto whitespace-pre-wrap text-foreground">
                    <code>{traceData.hops[selectedHopIdx].code_snippet}</code>
                  </pre>
                </div>

                {/* Narration */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-label font-medium text-foreground flex items-center gap-1.5">
                      <Sparkles className="w-4 h-4 text-warning" />
                      Explanation
                    </div>
                    {!expandedNarrations[selectedHopIdx] && (
                      <button
                        onClick={() => handleExpandNarration(selectedHopIdx)}
                        disabled={isStreamingNarration}
                        className="px-2.5 py-1 rounded-md text-meta font-medium text-primary bg-selected hover:bg-primary/10 border border-primary/20 flex items-center gap-1.5 transition-colors disabled:opacity-50"
                      >
                        {isStreamingNarration ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                        Detailed explanation
                      </button>
                    )}
                  </div>
                  <div className="p-4 rounded-lg bg-muted-surface border border-border text-body text-foreground leading-relaxed">
                    <StreamingText
                      text={expandedNarrations[selectedHopIdx] || traceData.hops[selectedHopIdx].narration || 'No explanation available for this step.'}
                      isStreaming={isStreamingNarration}
                    />
                  </div>
                </div>

                {/* Next call */}
                {traceData.hops[selectedHopIdx].next_call && (
                  <div className="flex items-center gap-2 text-meta text-muted-foreground pt-2 border-t border-border">
                    <span>Next:</span>
                    <span className="font-mono text-primary font-medium">{traceData.hops[selectedHopIdx].next_call}</span>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
