'use client';

import React, { useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/utils';
import { Check, Copy } from 'lucide-react';

function CodeBlock({ className, children, ...props }: any) {
  const [copied, setCopied] = React.useState(false);
  const codeString = String(children).replace(/\n$/, '');
  const language = className?.replace('language-', '') || '';

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(codeString).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [codeString]);

  if (!className) {
    // Inline code
    return (
      <code className="px-1.5 py-0.5 rounded bg-muted-surface text-code font-mono text-foreground" {...props}>
        {children}
      </code>
    );
  }

  return (
    <div className="relative group my-3 rounded-lg overflow-hidden border border-border">
      <div className="flex items-center justify-between px-3 py-1.5 bg-muted-surface border-b border-border">
        <span className="text-[11px] font-mono text-muted-foreground">{language || 'code'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-muted-foreground hover:text-foreground hover:bg-surface transition-colors"
          aria-label="Copy code"
        >
          {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <pre className="overflow-x-auto p-4 bg-surface text-code font-mono leading-relaxed">
        <code className={className} {...props}>{children}</code>
      </pre>
    </div>
  );
}

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content, className }) => {
  return (
    <div className={cn('prose-atlas', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code: CodeBlock,
          a: ({ href, children, ...props }) => (
            <a
              href={href}
              target={href?.startsWith('http') ? '_blank' : undefined}
              rel={href?.startsWith('http') ? 'noopener noreferrer' : undefined}
              className="text-primary underline underline-offset-2 hover:opacity-80 transition-opacity"
              {...props}
            >
              {children}
            </a>
          ),
          h1: ({ children }) => <h1 className="text-title font-semibold text-foreground mt-6 mb-3">{children}</h1>,
          h2: ({ children }) => <h2 className="text-section font-semibold text-foreground mt-5 mb-2">{children}</h2>,
          h3: ({ children }) => <h3 className="text-body font-semibold text-foreground mt-4 mb-2">{children}</h3>,
          p: ({ children }) => <p className="text-body text-foreground mb-3 leading-relaxed">{children}</p>,
          ul: ({ children }) => <ul className="list-disc pl-5 mb-3 space-y-1 text-body text-foreground">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-5 mb-3 space-y-1 text-body text-foreground">{children}</ol>,
          li: ({ children }) => <li className="text-body leading-relaxed">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-primary pl-4 py-1 my-3 text-muted-foreground italic">{children}</blockquote>
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto my-3 rounded-lg border border-border">
              <table className="w-full text-meta">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="px-3 py-2 text-left font-semibold bg-muted-surface text-foreground border-b border-border">{children}</th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-2 border-b border-border text-foreground">{children}</td>
          ),
          hr: () => <hr className="my-6 border-border" />,
          strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};
