'use client';

import React, { useState } from 'react';
import { FileCode, X, Copy, Check, ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface Citation {
  file_path: string;
  snippet?: string;
  chunk_id?: string | number;
  similarity?: number;
  line_start?: number;
  line_end?: number;
}

interface EvidenceInspectorProps {
  citation: Citation;
  onClose: () => void;
  onOpenFile?: (path: string) => void;
  className?: string;
}

export const EvidenceInspector: React.FC<EvidenceInspectorProps> = ({
  citation,
  onClose,
  onOpenFile,
  className,
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopySnippet = () => {
    if (!citation.snippet) return;
    navigator.clipboard.writeText(citation.snippet).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const fileName = citation.file_path ? citation.file_path.split('/').pop() : 'Source';

  return (
    <div
      role="region"
      aria-label="Source Evidence Inspector"
      className={cn(
        'w-full lg:w-[360px] shrink-0 border-l border-border bg-surface flex flex-col h-full overflow-hidden',
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <FileCode className="w-4 h-4 text-primary shrink-0" />
          <h3 className="text-label font-semibold text-foreground truncate">Source Evidence</h3>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors"
          aria-label="Close evidence panel"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* File information */}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-meta font-semibold text-foreground truncate">{fileName}</span>
            {onOpenFile && citation.file_path && (
              <button
                onClick={() => onOpenFile(citation.file_path)}
                className="text-[11px] text-primary hover:underline flex items-center gap-1 font-medium"
                title="Select file in explorer"
              >
                <span>Select file</span>
                <ExternalLink className="w-3 h-3" />
              </button>
            )}
          </div>
          <p className="text-meta font-mono text-muted-foreground break-all" title={citation.file_path}>
            {citation.file_path}
          </p>
          {citation.chunk_id !== undefined && (
            <p className="text-[11px] text-muted-foreground">
              Chunk reference: <span className="font-mono">{citation.chunk_id}</span>
            </p>
          )}
        </div>

        {/* Snippet */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Source snippet
            </span>
            {citation.snippet && (
              <button
                onClick={handleCopySnippet}
                className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors"
                aria-label="Copy snippet"
              >
                {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
                <span>{copied ? 'Copied' : 'Copy'}</span>
              </button>
            )}
          </div>

          {citation.snippet ? (
            <pre className="p-3.5 rounded-lg bg-muted-surface border border-border text-code font-mono overflow-x-auto whitespace-pre-wrap text-foreground leading-relaxed max-h-[380px]">
              {citation.snippet}
            </pre>
          ) : (
            <div className="p-4 rounded-lg bg-muted-surface border border-border text-meta text-muted-foreground italic text-center">
              No snippet content provided for this citation.
            </div>
          )}
        </div>

        {/* Retrieval relevance */}
        {citation.similarity !== undefined && (
          <div className="pt-2 border-t border-border flex items-center justify-between text-[11px] text-muted-foreground">
            <span>Retrieval relevance</span>
            <span className="font-mono font-medium text-foreground">
              {citation.similarity.toFixed(3)}
            </span>
          </div>
        )}
      </div>
    </div>
  );
};
