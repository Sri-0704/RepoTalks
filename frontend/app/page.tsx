'use client';

import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import Link from 'next/link';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FolderGit2,
  Upload,
  Send,
  FileCode,
  Code2,
  X,
  Square,
  Loader2,
  ChevronDown,
  ArrowDown,
  Clock,
  FileText,
  AlertCircle,
  RotateCcw,
  Settings as SettingsIcon,
  ExternalLink,
} from 'lucide-react';
import { FileExplorer } from '@/components/FileExplorer';
import { MarkdownRenderer } from '@/components/shared/MarkdownRenderer';
import { ImageUploader } from '@/components/ImageUploader';
import { EvidenceInspector } from '@/components/workspace/EvidenceInspector';
import { chatMultimodal, ingestGitHubRepo, ingestUploadZip, streamChat } from '@/lib/api';
import { useProject } from '@/context/ProjectContext';
import { cn } from '@/lib/utils';

interface MessageItem {
  id: string;
  sender: 'user' | 'assistant';
  text: string;
  citations?: any[];
  error?: boolean;
  interrupted?: boolean;
  repoId?: string;
}

// ─── Message Row ───
const MessageRow = React.memo(function MessageRow({
  msg,
  isStreamingActive,
  onCitationClick,
}: {
  msg: MessageItem;
  isStreamingActive: boolean;
  onCitationClick?: (citation: any) => void;
}) {
  if (msg.sender === 'user') {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[85%] bg-selected text-selected-foreground rounded-lg rounded-br-sm px-4 py-2.5 text-body">
          {msg.text}
        </div>
      </div>
    );
  }

  const hasText = Boolean(msg.text && msg.text.trim().length > 0);
  const showCitations = Boolean(msg.citations && msg.citations.length > 0);

  return (
    <div className="mb-6">
      {msg.error ? (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-danger/5 border border-danger/20 text-danger text-meta">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{msg.text || 'An error occurred.'}</span>
        </div>
      ) : (
        <MarkdownRenderer content={msg.text || (isStreamingActive ? '…' : '')} />
      )}
      {showCitations && (hasText || isStreamingActive || msg.error) && (
        <div className="mt-3 pt-3 border-t border-border flex flex-wrap gap-1.5">
          {msg.citations!.map((citation: any, idx: number) => (
            <button
              key={idx}
              onClick={() => onCitationClick?.(citation)}
              className="flex items-center gap-1 px-2 py-1 rounded-md bg-muted-surface border border-border text-meta text-muted-foreground hover:text-foreground hover:bg-selected transition-colors"
              title={citation.file_path}
            >
              <FileText className="w-3 h-3" />
              <span className="font-mono truncate max-w-[180px]">
                {citation.file_path?.split('/').pop() || `Source ${idx + 1}`}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
});

// ─── Main Page ───
export default function WorkspacePage() {
  const [repoUrl, setRepoUrl] = useState('');
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [showFiles, setShowFiles] = useState(true);

  const { activeRepo, setActiveRepoState, refreshProjects, status: projectStatus, error: projectError } = useProject();

  // Chat state — keyed per repo
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [inputMessage, setInputMessage] = useState('');
  const [selectedImage, setSelectedImage] = useState<File | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeCitation, setActiveCitation] = useState<any | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const userStoppedRef = useRef(false);
  const accumulatedRef = useRef('');
  const activeRepoIdRef = useRef<string | undefined>(activeRepo?.repo_id);
  activeRepoIdRef.current = activeRepo?.repo_id;
  const activeRequestIdRef = useRef<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Reset on repo switch — UX-03 fix
  const currentRepoId = activeRepo?.repo_id;
  const prevRepoIdRef = useRef<string | undefined>();
  useEffect(() => {
    if (prevRepoIdRef.current && prevRepoIdRef.current !== currentRepoId) {
      activeRequestIdRef.current = null;
      abortRef.current?.abort();
      setMessages([]);
      setInputMessage('');
      setSelectedFile(null);
      setSelectedImage(null);
      setIsStreaming(false);
      setActiveCitation(null);
    }
    prevRepoIdRef.current = currentRepoId;
  }, [currentRepoId]);

  // Cleanup abort on unmount
  useEffect(() => {
    return () => {
      activeRequestIdRef.current = null;
      try {
        abortRef.current?.abort();
      } catch {
        // noop
      }
      abortRef.current = null;
    };
  }, []);

  // Listen for file selection from search or external panels
  useEffect(() => {
    const handleSelectFileEvent = (e: any) => {
      if (e.detail?.path) {
        setSelectedFile(e.detail.path);
        setShowFiles(true);
      }
    };
    window.addEventListener('repotalks:select-file', handleSelectFileEvent);
    return () => window.removeEventListener('repotalks:select-file', handleSelectFileEvent);
  }, []);

  // Elapsed timer during ingestion
  useEffect(() => {
    if (!ingesting) { setElapsedSeconds(0); return; }
    const start = Date.now();
    const interval = setInterval(() => setElapsedSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(interval);
  }, [ingesting]);

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, autoScroll]);

  const handleChatScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    setAutoScroll(isNearBottom);
  }, []);

  // ─── Ingestion ───
  const handleGitHubSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const url = repoUrl.trim();
    if (!url || ingesting) return;

    if (!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+/i.test(url)) {
      setIngestError('Please enter a valid public GitHub repository URL (e.g. https://github.com/owner/repository).');
      return;
    }

    setIngesting(true);
    setIngestError(null);
    try {
      const data = await ingestGitHubRepo(url);
      setActiveRepoState(data.project);
      setRepoUrl('');
      await refreshProjects();
      setMessages([{
        id: 'welcome_' + Date.now(),
        sender: 'assistant',
        text: `Repository **${data.project.name}** is ready. ${data.project.file_count} files indexed.\n\nAsk me anything about the codebase.`,
        repoId: data.project.repo_id,
      }]);
    } catch (err: any) {
      setIngestError(err.message || 'GitHub ingestion failed');
    } finally {
      setIngesting(false);
    }
  };

  const handleRetryIngestion = () => {
    if (repoUrl.trim() && !ingesting) {
      handleGitHubSubmit();
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || ingesting) return;
    setIngesting(true);
    setIngestError(null);
    try {
      const data = await ingestUploadZip(file);
      setActiveRepoState(data.project);
      await refreshProjects();
      setMessages([{
        id: 'welcome_' + Date.now(),
        sender: 'assistant',
        text: `Archive **${data.project.name}** processed. ${data.project.file_count} files indexed.\n\nAsk anything about your code.`,
        repoId: data.project.repo_id,
      }]);
    } catch (err: any) {
      setIngestError(err.message || 'ZIP upload failed');
    } finally {
      setIngesting(false);
    }
  };

  // ─── Chat ───
  const handleStopStreaming = useCallback(() => {
    userStoppedRef.current = true;
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const handleSendMessage = useCallback(async () => {
    if (!inputMessage.trim() || !activeRepo || isStreaming) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    userStoppedRef.current = false;
    accumulatedRef.current = '';
    const repoId = activeRepo.repo_id;
    const userText = inputMessage.trim();
    setInputMessage('');
    setAutoScroll(true);

    const userMsgId = 'user_' + Date.now();
    const assistantMsgId = 'asst_' + Date.now();
    const requestId = 'req_' + Date.now() + '_' + Math.random().toString(36).substring(2, 7);
    activeRequestIdRef.current = requestId;

    setMessages((prev) => [
      ...prev,
      { id: userMsgId, sender: 'user', text: userText, repoId },
      { id: assistantMsgId, sender: 'assistant', text: '', repoId },
    ]);
    setIsStreaming(true);

    const updateAssistant = (update: Partial<MessageItem>) => {
      if (activeRepoIdRef.current !== repoId || activeRequestIdRef.current !== requestId) return;
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === assistantMsgId && m.repoId === repoId);
        if (idx === -1) return prev;
        const newMsgs = [...prev];
        newMsgs[idx] = { ...newMsgs[idx], ...update };
        return newMsgs;
      });
    };

    try {
      if (selectedImage) {
        const response = await chatMultimodal(activeRepo.repo_id, userText, selectedImage, controller.signal);
        updateAssistant({ text: response.text, citations: response.citations });
        setSelectedImage(null);
      } else {
        let accumulated = '';
        let flushTimer: any = null;

        const scheduleFlush = () => {
          if (!flushTimer) {
            flushTimer = setTimeout(() => {
              updateAssistant({ text: accumulated });
              flushTimer = null;
            }, 40);
          }
        };

        const history = messages.slice(-10).map(({ sender, text }) => ({ sender, text }));
        const outcome = await streamChat(
          activeRepo.repo_id,
          userText,
          (chunk) => {
            accumulated += chunk;
            accumulatedRef.current = accumulated;
            scheduleFlush();
          },
          (citations) => {
            if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
            updateAssistant({ text: accumulated, citations });
          },
          selectedFile || undefined,
          history,
          controller.signal,
        );

        if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }

        if (activeRepoIdRef.current !== repoId || activeRequestIdRef.current !== requestId) return;

        if (outcome.type === 'completed') {
          const trimmed = accumulated.trim();
          if (!trimmed || !outcome.contentReceived) {
            updateAssistant({
              text: 'The AI provider completed without returning an answer. Please retry your question or switch the LLM Provider in Settings.',
              error: true,
            });
          } else {
            updateAssistant({ text: accumulated });
          }
        } else if (outcome.type === 'aborted') {
          if (userStoppedRef.current) {
            const currentText = accumulatedRef.current.trim();
            if (!currentText) {
              updateAssistant({
                text: 'Response stopped before any text arrived.',
                interrupted: true,
                error: false,
              });
            } else {
              updateAssistant({
                text: accumulatedRef.current + '\n\n*(Response stopped by user)*',
                interrupted: true,
              });
            }
          }
        } else if (outcome.type === 'interrupted') {
          const currentText = accumulated.trim();
          if (currentText) {
            updateAssistant({
              text: accumulated + '\n\n*(Stream connection interrupted)*',
              interrupted: true,
              error: true,
            });
          } else {
            updateAssistant({
              text: `Stream connection interrupted: ${outcome.reason} Please retry.`,
              error: true,
            });
          }
        }
      }
    } catch (err: any) {
      if (activeRepoIdRef.current !== repoId || activeRequestIdRef.current !== requestId) return;
      if (err?.name === 'AbortError' || controller.signal.aborted) {
        if (userStoppedRef.current) {
          const currentText = accumulatedRef.current.trim();
          if (!currentText) {
            updateAssistant({
              text: 'Response stopped before any text arrived.',
              interrupted: true,
              error: false,
            });
          } else {
            updateAssistant({
              text: accumulatedRef.current + '\n\n*(Response stopped by user)*',
              interrupted: true,
            });
          }
        }
        return;
      }
      updateAssistant({ text: err.message || 'Request failed', error: true });
    } finally {
      if (activeRequestIdRef.current === requestId) {
        setIsStreaming(false);
      }
    }
  }, [inputMessage, activeRepo, isStreaming, selectedImage, selectedFile, messages]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !(e as any).isComposing) {
      e.preventDefault();
      handleSendMessage();
    }
  }, [handleSendMessage]);

  const handleFileSelect = useCallback((path: string) => {
    setSelectedFile(path);
    // Don't overwrite existing typed question — UX-03 fix
    if (!inputMessage.trim()) {
      setInputMessage(`Explain the purpose and structure of ${path}`);
    }
  }, [inputMessage]);

  // ─── Render: First-run state ───
  if (!activeRepo) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="w-full max-w-xl space-y-6">
          <div className="text-center space-y-2">
            <h1 className="text-headline text-foreground">Understand your repository</h1>
            <p className="text-body text-muted-foreground">
              Ask about the code, follow a flow, and prepare to explain it.
            </p>
          </div>

          <div className="bg-surface border border-border rounded-xl p-6 space-y-4">
            <form onSubmit={handleGitHubSubmit} className="space-y-3">
              <label htmlFor="repo-url" className="text-label font-medium text-foreground block">
                Public GitHub repository URL
              </label>
              <div className="flex gap-2">
                <input
                  id="repo-url"
                  type="text"
                  placeholder="https://github.com/org/repo"
                  value={repoUrl}
                  onChange={(e) => { setRepoUrl(e.target.value); setIngestError(null); }}
                  disabled={ingesting}
                  className="flex-1 px-3 py-2.5 rounded-md bg-muted-surface border border-border text-body text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={ingesting || !repoUrl.trim()}
                  className="px-4 py-2.5 rounded-md bg-primary text-primary-foreground font-medium text-label hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-2 shrink-0"
                >
                  {ingesting ? <Loader2 className="w-4 h-4 animate-spin" /> : <FolderGit2 className="w-4 h-4" />}
                  Add repository
                </button>
              </div>
            </form>

            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-meta text-muted-foreground">or</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            <label className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-md border border-dashed border-border text-label text-muted-foreground hover:text-foreground hover:border-primary cursor-pointer transition-colors">
              <Upload className="w-4 h-4" />
              <span>Upload ZIP archive</span>
              <input type="file" accept=".zip" onChange={handleFileUpload} className="hidden" disabled={ingesting} />
            </label>

            {/* Ingestion status */}
            {ingesting && (
              <div className="flex items-center gap-3 p-3 rounded-md bg-muted-surface border border-border">
                <Loader2 className="w-4 h-4 animate-spin text-primary" />
                <div className="flex-1 min-w-0">
                  <p className="text-label font-medium text-foreground">Preparing your repository…</p>
                  <p className="text-meta text-muted-foreground">Larger repositories may take longer.</p>
                </div>
                <div className="flex items-center gap-1 text-meta text-muted-foreground">
                  <Clock className="w-3 h-3" />
                  <span>{elapsedSeconds}s</span>
                </div>
              </div>
            )}

            {/* Error */}
            {ingestError && (
              <div className="flex flex-col gap-2.5 p-3.5 rounded-md bg-danger/5 border border-danger/20">
                <div className="flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <p className="text-meta text-danger font-medium">{ingestError}</p>
                    {(ingestError.toLowerCase().includes('quota') || ingestError.toLowerCase().includes('resource_exhausted')) && (
                      <div className="mt-2 text-xs text-muted-foreground bg-surface/60 p-2.5 rounded border border-border/50">
                        <p className="font-medium text-foreground mb-1">Project-level Quota Notice:</p>
                        <p className="leading-relaxed">
                          Gemini quotas are enforced at the Google Cloud / AI Studio <strong>project</strong> level. Creating a new API key within the same project shares the same quota limit. To resolve this, wait for the quota window to reset, check your plan/billing, or configure an API key from an authorized project with available quota.
                        </p>
                        <div className="mt-2">
                          <a
                            href="https://aistudio.google.com/"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-primary hover:underline font-medium"
                          >
                            <span>Manage Google AI Studio Quotas & Billing</span>
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        </div>
                      </div>
                    )}
                  </div>
                  <button onClick={() => setIngestError(null)} className="text-danger hover:opacity-70 shrink-0" aria-label="Dismiss error">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="flex items-center gap-2 pt-1 border-t border-danger/10">
                  {repoUrl.trim() && (
                    <button
                      onClick={handleRetryIngestion}
                      disabled={ingesting}
                      className="px-2.5 py-1 text-xs font-medium rounded bg-primary text-primary-foreground hover:opacity-90 flex items-center gap-1.5 transition-opacity disabled:opacity-50"
                    >
                      <RotateCcw className="w-3 h-3" />
                      <span>Retry Ingestion</span>
                    </button>
                  )}
                  <Link
                    href="/settings"
                    className="px-2.5 py-1 text-xs font-medium rounded border border-border text-foreground hover:bg-muted-surface flex items-center gap-1.5 transition-colors"
                  >
                    <SettingsIcon className="w-3 h-3 text-muted-foreground" />
                    <span>Open Settings</span>
                  </Link>
                </div>
              </div>
            )}
          </div>

          {/* Project status */}
          {projectStatus === 'error' && (
            <div className="flex items-center gap-2 p-3 rounded-md bg-danger/5 border border-danger/20 text-meta text-danger">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span className="flex-1">{projectError || 'Unable to connect to backend'}</span>
              <button onClick={refreshProjects} className="flex items-center gap-1 text-primary hover:underline">
                <RotateCcw className="w-3 h-3" /> Retry
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ─── Render: Active workspace ───
  return (
    <div className="flex gap-0 -mx-4 md:-mx-6 -mb-4 md:-mb-6" style={{ height: 'calc(100vh - 57px - 24px)' }}>
      {/* Files pane */}
      <AnimatePresence>
        {showFiles && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 240, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="hidden lg:flex flex-col border-r border-border bg-surface shrink-0 overflow-hidden"
          >
            <div className="flex items-center justify-between px-3 py-2.5 border-b border-border shrink-0">
              <span className="text-label font-medium text-foreground">Files</span>
              <button
                onClick={() => setShowFiles(false)}
                className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors"
                aria-label="Hide files"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <FileExplorer
              tree={activeRepo.tree}
              onSelectFile={handleFileSelect}
              selectedFilePath={selectedFile || undefined}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Chat pane */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Chat toolbar */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-border shrink-0 bg-surface">
          {!showFiles && (
            <button
              onClick={() => setShowFiles(true)}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors hidden lg:flex"
              aria-label="Show files"
            >
              <FolderGit2 className="w-4 h-4" />
            </button>
          )}
          <span className="text-label font-medium text-foreground flex items-center gap-1.5">
            <Code2 className="w-4 h-4 text-primary" />
            Ask about {activeRepo.name}
          </span>
        </div>

        {/* Messages */}
        <div
          className="flex-1 overflow-y-auto px-4 md:px-6 py-4"
          onScroll={handleChatScroll}
        >
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <Code2 className="w-10 h-10 text-muted-foreground/30 mb-3" />
              <h3 className="text-label font-semibold text-foreground">Ready to explore</h3>
              <p className="text-meta text-muted-foreground max-w-sm mt-1">
                Ask any question about the codebase, or select a file from the tree.
              </p>
            </div>
          ) : (
            <div className="max-w-prose mx-auto">
              {messages.map((msg) => (
                <MessageRow
                  key={msg.id}
                  msg={msg}
                  isStreamingActive={isStreaming && msg.id === messages[messages.length - 1]?.id && msg.sender === 'assistant'}
                  onCitationClick={setActiveCitation}
                />
              ))}
              <div ref={chatEndRef} />
            </div>
          )}

          {/* Jump to latest */}
          {!autoScroll && messages.length > 0 && (
            <button
              onClick={() => { setAutoScroll(true); chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }}
              className="fixed bottom-24 right-8 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-surface border border-border shadow-lg text-meta text-foreground hover:bg-muted-surface transition-colors z-10"
            >
              <ArrowDown className="w-3 h-3" />
              Jump to latest
            </button>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-border px-4 py-3 bg-surface shrink-0">
          {/* Context chips */}
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            {selectedFile && (
              <span className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-selected text-selected-foreground text-meta font-mono">
                <FileCode className="w-3 h-3" />
                <span className="truncate max-w-[200px]">{selectedFile}</span>
                <button
                  onClick={() => setSelectedFile(null)}
                  className="p-0.5 rounded hover:bg-primary/20 transition-colors"
                  aria-label="Clear file context"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            )}
            <ImageUploader onImageSelected={setSelectedImage} selectedImage={selectedImage} />
            {selectedImage && (
              <span className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-muted-surface text-meta text-foreground">
                📎 {selectedImage.name}
                <button onClick={() => setSelectedImage(null)} className="p-0.5 rounded hover:bg-border transition-colors" aria-label="Remove image">
                  <X className="w-3 h-3" />
                </button>
              </span>
            )}
          </div>

          {/* Input */}
          <div className="flex items-end gap-2">
            <textarea
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask a question about the codebase…"
              rows={1}
              className="flex-1 px-3 py-2.5 rounded-md bg-muted-surface border border-border text-body text-foreground placeholder:text-muted-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring min-h-[42px] max-h-[160px]"
              style={{ fieldSizing: 'content' } as any}
              disabled={isStreaming}
            />
            <button
              onClick={isStreaming ? handleStopStreaming : handleSendMessage}
              disabled={!isStreaming && !inputMessage.trim()}
              className={cn(
                'p-2.5 rounded-md transition-all shrink-0',
                isStreaming
                  ? 'bg-danger text-white hover:opacity-90'
                  : 'bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-30'
              )}
              aria-label={isStreaming ? 'Stop generating' : 'Send message'}
            >
              {isStreaming ? <Square className="w-4 h-4" /> : <Send className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </div>

      {/* Evidence Inspector */}
      <AnimatePresence>
        {activeCitation && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 360, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="hidden lg:block overflow-hidden"
          >
            <EvidenceInspector
              citation={activeCitation}
              onClose={() => setActiveCitation(null)}
              onOpenFile={(path) => {
                setSelectedFile(path);
                setShowFiles(true);
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
