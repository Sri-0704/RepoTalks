'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  Users,
  Briefcase,
  UserCheck,
  Code,
  GraduationCap,
  Play,
  Loader2,
  CheckCircle2,
  MessageSquare,
  Sparkles,
  Copy,
  Check,
  AlertCircle,
} from 'lucide-react';
import { fetchAudienceExplanation } from '@/lib/api';
import { useProject } from '@/context/ProjectContext';
import { cn } from '@/lib/utils';

const AUDIENCE_OPTIONS = [
  { id: 'developer', label: 'Fellow Developer', icon: Code, desc: 'Design trade-offs, data flow, API contracts' },
  { id: 'recruiter', label: 'Tech Recruiter', icon: Briefcase, desc: 'Tech stack, impact, business value' },
  { id: 'manager', label: 'Non-Technical Manager', icon: UserCheck, desc: 'Zero-jargon analogies, project ROI' },
  { id: 'professor', label: 'College Professor', icon: GraduationCap, desc: 'Algorithms, data structures, rigor' },
];

export default function AudiencePage() {
  const { activeRepo } = useProject();
  const [selectedAudience, setSelectedAudience] = useState('developer');
  const [explanation, setExplanation] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRequested, setHasRequested] = useState(false);
  const [copied, setCopied] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const requestRepoRef = useRef<string | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    setExplanation(null);
    setHasRequested(false);
    setError(null);
  }, [activeRepo?.repo_id]);

  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);

  const loadExplanation = async (repoId: string, audienceId: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    requestRepoRef.current = repoId;

    setLoading(true);
    setHasRequested(true);
    setError(null);

    try {
      const data = await fetchAudienceExplanation(repoId, audienceId, undefined, controller.signal);
      // Reject stale response — UX-04 fix
      if (requestRepoRef.current !== repoId) return;
      if (!data || data.error) {
        throw new Error(data?.error || 'Failed to generate explanation');
      }
      setExplanation(data);
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setError(err.message || 'Failed to generate explanation');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleAudienceChange = (id: string) => {
    setSelectedAudience(id);
    // Don't auto-trigger if user hasn't requested yet
    if (activeRepo && hasRequested) {
      loadExplanation(activeRepo.repo_id, id);
    }
  };

  const handleCopyExplanation = () => {
    if (!explanation) return;
    const text = [
      explanation.headline,
      '',
      'Elevator Pitch:',
      explanation.elevator_pitch,
      '',
      'Key Highlights:',
      ...(explanation.key_highlights || []).map((h: string) => `• ${h}`),
      '',
      'Detailed Explanation:',
      explanation.detailed_explanation,
      '',
      'Talking Points:',
      ...(explanation.talking_points || []).map((t: string) => `• "${t}"`),
    ].join('\n');
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  if (!activeRepo) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] text-center">
        <Users className="w-10 h-10 text-muted-foreground/30 mb-3" />
        <h3 className="text-label font-semibold text-foreground">No repository selected</h3>
        <p className="text-meta text-muted-foreground mt-1">Add or select a repository first.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-surface border border-border rounded-xl p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
        <div>
          <h2 className="text-section text-foreground flex items-center gap-2">
            <Users className="w-5 h-5 text-primary" />
            Explain this project
          </h2>
          <p className="text-meta text-muted-foreground mt-0.5">
            Same codebase, tailored for different audiences.
          </p>
        </div>

        <button
          onClick={() => activeRepo && loadExplanation(activeRepo.repo_id, selectedAudience)}
          disabled={loading}
          className="px-4 py-2.5 rounded-md bg-primary text-primary-foreground font-medium text-label hover:opacity-90 disabled:opacity-50 flex items-center gap-2 shrink-0 transition-opacity"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          Generate explanation
        </button>
      </div>

      {/* Audience selector (accessible radio group) */}
      <div role="radiogroup" aria-label="Select audience" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {AUDIENCE_OPTIONS.map((aud) => {
          const isSelected = selectedAudience === aud.id;
          const Icon = aud.icon;
          return (
            <button
              key={aud.id}
              role="radio"
              aria-checked={isSelected}
              onClick={() => handleAudienceChange(aud.id)}
              className={cn(
                'p-4 rounded-xl border text-left transition-colors',
                isSelected
                  ? 'bg-selected border-primary/30 ring-2 ring-ring ring-offset-1'
                  : 'bg-surface border-border hover:bg-muted-surface'
              )}
            >
              <div className="flex items-center gap-3">
                <div className={cn(
                  'w-9 h-9 rounded-lg flex items-center justify-center shrink-0',
                  isSelected ? 'bg-primary text-primary-foreground' : 'bg-muted-surface text-muted-foreground'
                )}>
                  <Icon className="w-4 h-4" />
                </div>
                <div className="min-w-0">
                  <h4 className="text-label font-medium text-foreground">{aud.label}</h4>
                  <p className="text-meta text-muted-foreground mt-0.5">{aud.desc}</p>
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 p-4 rounded-xl bg-danger/5 border border-danger/20 text-meta text-danger">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* Output */}
      {!hasRequested ? (
        <div className="flex flex-col items-center justify-center py-20 bg-surface border border-border rounded-xl text-center">
          <Users className="w-10 h-10 text-muted-foreground/30 mb-3" />
          <h3 className="text-label font-semibold text-foreground">Ready for {activeRepo.name}</h3>
          <p className="text-meta text-muted-foreground max-w-sm mt-1">
            Select an audience and click "Generate explanation."
          </p>
        </div>
      ) : loading ? (
        <div className="flex flex-col items-center justify-center py-24 bg-surface border border-border rounded-xl">
          <Loader2 className="w-8 h-8 animate-spin text-primary mb-3" />
          <h4 className="text-label font-semibold text-foreground">Tailoring explanation…</h4>
          <p className="text-meta text-muted-foreground">Formatting for {selectedAudience}</p>
        </div>
      ) : explanation ? (
        <div className="bg-surface border border-border rounded-xl p-6 space-y-5">
          {/* Headline & Pitch */}
          <div className="pb-5 border-b border-border space-y-3">
            <div className="flex items-center justify-between">
              <span className="px-2 py-0.5 rounded bg-selected text-selected-foreground text-meta font-medium uppercase tracking-wider">
                {explanation.audience}
              </span>
              <button
                onClick={handleCopyExplanation}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-meta text-muted-foreground hover:text-foreground hover:bg-muted-surface border border-border transition-colors"
              >
                {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
                <span>{copied ? 'Copied' : 'Copy'}</span>
              </button>
            </div>

            <h3 className="text-title text-foreground leading-snug">
              "{explanation.headline}"
            </h3>

            <div className="p-4 rounded-lg bg-muted-surface border border-border text-body text-foreground leading-relaxed">
              <span className="font-semibold text-foreground block mb-1">Elevator pitch</span>
              {explanation.elevator_pitch}
            </div>
          </div>

          {/* Key Highlights */}
          {explanation.key_highlights && explanation.key_highlights.length > 0 && (
            <div className="space-y-3">
              <h4 className="text-label font-medium text-foreground uppercase tracking-wider flex items-center gap-1.5">
                <Sparkles className="w-4 h-4 text-warning" />
                Key takeaways
              </h4>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {explanation.key_highlights.map((hl: string, idx: number) => (
                  <div key={idx} className="p-3 rounded-lg bg-muted-surface border border-border text-meta text-foreground flex items-start gap-2">
                    <CheckCircle2 className="w-4 h-4 text-success shrink-0 mt-0.5" />
                    <span>{hl}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Detailed Explanation */}
          <div className="space-y-2">
            <h4 className="text-label font-medium text-foreground uppercase tracking-wider">Detailed explanation</h4>
            <div className="p-5 rounded-lg bg-muted-surface border border-border text-body text-foreground leading-relaxed whitespace-pre-wrap">
              {explanation.detailed_explanation}
            </div>
          </div>

          {/* Talking Points */}
          {explanation.talking_points && explanation.talking_points.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-label font-medium text-foreground uppercase tracking-wider flex items-center gap-1.5">
                <MessageSquare className="w-4 h-4 text-primary" />
                Interview talking points
              </h4>
              <div className="space-y-2">
                {explanation.talking_points.map((tp: string, idx: number) => (
                  <div key={idx} className="p-3 rounded-lg bg-muted-surface border border-border text-meta text-foreground">
                    "{tp}"
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
