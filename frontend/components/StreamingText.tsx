'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface StreamingTextProps {
  text: string;
  isStreaming?: boolean;
  className?: string;
}

export const StreamingText: React.FC<StreamingTextProps> = ({
  text,
  isStreaming = false,
  className = '',
}) => {
  return (
    <div className={`prose prose-slate max-w-none text-xs md:text-sm leading-relaxed ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          strong: ({ children }) => (
            <strong className="font-semibold text-slate-900 dark:text-slate-100">
              {children}
            </strong>
          ),
          code({ node, inline, className, children, ...props }: any) {
            if (inline) {
              return (
                <code
                  className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-indigo-700 dark:text-indigo-300 font-mono text-[11px] border border-slate-200/60 dark:border-slate-700/60"
                  {...props}
                >
                  {children}
                </code>
              );
            }
            return (
              <pre className="p-3.5 my-2.5 rounded-xl bg-slate-900 dark:bg-slate-950 text-emerald-400 font-mono text-[11px] overflow-x-auto shadow-inner border border-slate-800 leading-normal">
                <code className={className} {...props}>
                  {children}
                </code>
              </pre>
            );
          },
          ul: ({ children }) => <ul className="list-disc list-inside space-y-1 my-2 text-slate-700 dark:text-slate-200">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal list-inside space-y-1 my-2 text-slate-700 dark:text-slate-200">{children}</ol>,
          li: ({ children }) => <li className="text-slate-700 dark:text-slate-200 leading-snug">{children}</li>,
          h1: ({ children }) => <h1 className="text-base font-bold text-slate-900 dark:text-slate-100 my-2">{children}</h1>,
          h2: ({ children }) => <h2 className="text-sm font-bold text-slate-900 dark:text-slate-100 my-1.5">{children}</h2>,
          h3: ({ children }) => <h3 className="text-xs font-bold text-slate-900 dark:text-slate-100 my-1">{children}</h3>,
          p: ({ children }) => <p className="my-1.5 leading-relaxed text-slate-700 dark:text-slate-200">{children}</p>,
        }}
      >
        {text}
      </ReactMarkdown>

      {isStreaming && (
        <span className="inline-block w-2 h-4 ml-1 bg-indigo-600 animate-pulse rounded-sm align-middle" />
      )}
    </div>
  );
};

