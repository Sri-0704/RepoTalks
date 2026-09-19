'use client';

import React, { useState, useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import {
  BookOpen,
  Mic,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  Play,
  RotateCcw,
  Loader2,
  Download,
  Printer,
  FileCode,
  Search,
  RefreshCw,
  FileText,
  Award,
  X,
} from 'lucide-react';
import { VoiceRecorder } from '@/components/VoiceRecorder';
import { fetchVivaQuestion, evaluateVivaAnswer, fetchVivaSummary, fetchVivaQuestionBank } from '@/lib/api';
import { useProject } from '@/context/ProjectContext';
import { cn } from '@/lib/utils';

const CATEGORY_COLORS: Record<string, string> = {
  'Architecture & Design Decisions': 'bg-primary/10 text-primary border-primary/20',
  'Code Walkthrough': 'bg-selected text-selected-foreground border-primary/20',
  'Trade-offs & Alternatives': 'bg-warning/10 text-warning border-warning/20',
  'Edge Cases & Failure Modes': 'bg-danger/10 text-danger border-danger/20',
  'Security & Performance': 'bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20',
};

export default function VivaPage() {
  const { activeRepo } = useProject();
  const [activeTab, setActiveTab] = useState<'study' | 'live'>('study');
  const [difficulty, setDifficulty] = useState<'easy' | 'medium' | 'hard'>('medium');

  // Question bank state
  const [questionBank, setQuestionBank] = useState<any | null>(null);
  const [isGeneratingBank, setIsGeneratingBank] = useState(false);
  const [questionStatuses, setQuestionStatuses] = useState<Record<string, 'practiced' | 'shaky' | 'unmarked'>>({});
  const [categoryFilter, setCategoryFilter] = useState<string>('All');
  const [searchQuery, setSearchQuery] = useState('');

  // Live session state
  const [currentQuestion, setCurrentQuestion] = useState<any | null>(null);
  const [isGeneratingQ, setIsGeneratingQ] = useState(false);
  const [evaluation, setEvaluation] = useState<any | null>(null);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [sessionHistory, setSessionHistory] = useState<any[]>([]);
  const [summary, setSummary] = useState<any | null>(null);
  const [isGeneratingSummary, setIsGeneratingSummary] = useState(false);
  const [vivaError, setVivaError] = useState<string | null>(null);

  useEffect(() => {
    setQuestionBank(null);
    setCurrentQuestion(null);
    setEvaluation(null);
    setSessionHistory([]);
    setSummary(null);
    setCategoryFilter('All');
    setSearchQuery('');
    setVivaError(null);
    if (!activeRepo) { setQuestionStatuses({}); return; }
    const storageKey = `repotalks_qbank_status_${activeRepo.repo_id}`;
    const saved = localStorage.getItem(storageKey);
    if (saved) {
      try { setQuestionStatuses(JSON.parse(saved)); } catch { setQuestionStatuses({}); }
    } else {
      setQuestionStatuses({});
    }
  }, [activeRepo?.repo_id]);

  const handleLoadQuestionBank = async () => {
    if (!activeRepo) return;
    setIsGeneratingBank(true);
    setVivaError(null);
    try {
      const bankData = await fetchVivaQuestionBank(activeRepo.repo_id);
      setQuestionBank(bankData);
    } catch (err: any) {
      setVivaError(err.message || 'Failed to generate question bank');
    } finally {
      setIsGeneratingBank(false);
    }
  };

  const updateQuestionStatus = (qId: string, status: 'practiced' | 'shaky' | 'unmarked') => {
    if (!activeRepo) return;
    setQuestionStatuses((prev) => {
      const next = { ...prev, [qId]: status };
      localStorage.setItem(`repotalks_qbank_status_${activeRepo.repo_id}`, JSON.stringify(next));
      return next;
    });
  };

  const handleExportMarkdown = () => {
    if (!questionBank || !activeRepo) return;
    let md = `# Interview Preparation: ${activeRepo.name}\n\n`;
    const total = questionBank.questions.length;
    const practiced = Object.values(questionStatuses).filter((s) => s === 'practiced').length;
    md += `Progress: ${practiced}/${total} practiced\n\n---\n\n`;
    (questionBank.categories || []).forEach((cat: string) => {
      const catQs = questionBank.questions.filter((q: any) => q.category === cat);
      if (!catQs.length) return;
      md += `## ${cat}\n\n`;
      catQs.forEach((q: any, i: number) => {
        const status = questionStatuses[q.id] || 'unmarked';
        md += `### Q${i + 1}: ${q.question} [${status}]\n`;
        md += `**Answer:** "${q.model_answer}"\n\n`;
        if (q.files_referenced?.length) md += `Files: ${q.files_referenced.join(', ')}\n\n`;
        md += `---\n\n`;
      });
    });
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `prep_${activeRepo.repo_id}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  // Live mode handlers
  const handleNextQuestion = async () => {
    if (!activeRepo) return;
    setIsGeneratingQ(true);
    setEvaluation(null);
    setVivaError(null);
    try {
      const qData = await fetchVivaQuestion(activeRepo.repo_id, '', difficulty, sessionHistory);
      setCurrentQuestion(qData);
    } catch (err: any) {
      setVivaError(err.message || 'Failed to fetch viva question');
    } finally {
      setIsGeneratingQ(false);
    }
  };

  const handleAnswerSubmit = async (answerText: string) => {
    if (!activeRepo || !currentQuestion || isEvaluating) return;
    setIsEvaluating(true);
    setVivaError(null);
    try {
      const evalData = await evaluateVivaAnswer(activeRepo.repo_id, currentQuestion.question, answerText, currentQuestion.context_file);
      setEvaluation(evalData);
      setSessionHistory((prev) => [...prev, {
        question: currentQuestion.question,
        student_answer: answerText,
        score: evalData.score,
        verdict: evalData.verdict,
        critique: evalData.critique,
      }]);
    } catch (err: any) {
      setVivaError(err.message || 'Failed to evaluate answer');
    } finally {
      setIsEvaluating(false);
    }
  };

  const handleFinishSession = async () => {
    if (!activeRepo || sessionHistory.length === 0) return;
    setIsGeneratingSummary(true);
    setVivaError(null);
    try {
      const sumData = await fetchVivaSummary(activeRepo.repo_id, sessionHistory);
      setSummary(sumData);
    } catch (err: any) {
      setVivaError(err.message || 'Failed to generate session summary');
    } finally {
      setIsGeneratingSummary(false);
    }
  };

  if (!activeRepo) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] text-center">
        <BookOpen className="w-10 h-10 text-muted-foreground/30 mb-3" />
        <h3 className="text-label font-semibold text-foreground">No repository selected</h3>
        <p className="text-meta text-muted-foreground mt-1">Add or select a repository first.</p>
      </div>
    );
  }

  const totalQuestions = questionBank?.questions?.length || 0;
  const practicedCount = Object.values(questionStatuses).filter((s) => s === 'practiced').length;
  const shakyCount = Object.values(questionStatuses).filter((s) => s === 'shaky').length;
  const progressPercent = totalQuestions > 0 ? Math.round((practicedCount / totalQuestions) * 100) : 0;

  const filteredQuestions = (questionBank?.questions || []).filter((q: any) => {
    const matchCat = categoryFilter === 'All' || q.category === categoryFilter;
    const matchQ = !searchQuery ||
      q.question.toLowerCase().includes(searchQuery.toLowerCase()) ||
      q.model_answer.toLowerCase().includes(searchQuery.toLowerCase());
    return matchCat && matchQ;
  });

  return (
    <div className="space-y-4">
      {vivaError && (
        <div className="bg-danger/10 border border-danger/20 rounded-lg p-3 flex items-start justify-between gap-2 text-danger">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span className="text-sm font-medium">{vivaError}</span>
          </div>
          <button
            onClick={() => setVivaError(null)}
            className="text-danger hover:opacity-80 p-0.5 rounded transition-opacity"
            aria-label="Dismiss error"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Header + Tab Switcher */}
      <div className="bg-surface border border-border rounded-xl p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 no-print">
        <div>
          <h2 className="text-section text-foreground flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-primary" />
            Practice
          </h2>
          <p className="text-meta text-muted-foreground mt-0.5">
            Study questions or rehearse with a mock interview.
          </p>
        </div>

        <div className="flex items-center gap-0.5 p-0.5 rounded-md bg-muted-surface border border-border">
          <button
            onClick={() => setActiveTab('study')}
            className={cn(
              'px-3 py-1.5 rounded text-meta font-medium transition-colors flex items-center gap-1.5',
              activeTab === 'study'
                ? 'bg-surface text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <FileText className="w-3.5 h-3.5" />
            Study
          </button>
          <button
            onClick={() => setActiveTab('live')}
            className={cn(
              'px-3 py-1.5 rounded text-meta font-medium transition-colors flex items-center gap-1.5',
              activeTab === 'live'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <Mic className="w-3.5 h-3.5" />
            Mock viva
          </button>
        </div>
      </div>

      {/* ═══ STUDY TAB ═══ */}
      {activeTab === 'study' && (
        <div className="space-y-4">
          {!questionBank && !isGeneratingBank ? (
            <div className="flex flex-col items-center justify-center py-20 bg-surface border border-border rounded-xl text-center">
              <BookOpen className="w-12 h-12 text-muted-foreground/30 mb-4" />
              <h3 className="text-label font-semibold text-foreground">
                Question bank for {activeRepo.name}
              </h3>
              <p className="text-meta text-muted-foreground max-w-sm mt-1">
                Generate interview questions grounded in the actual source code.
              </p>
              <button
                onClick={handleLoadQuestionBank}
                className="mt-4 px-4 py-2 rounded-md bg-primary text-primary-foreground font-medium text-label hover:opacity-90 transition-opacity flex items-center gap-2"
              >
                <Sparkles className="w-4 h-4" />
                Generate questions
              </button>
            </div>
          ) : isGeneratingBank ? (
            <div className="flex flex-col items-center justify-center py-20 bg-surface border border-border rounded-xl">
              <Loader2 className="w-8 h-8 animate-spin text-primary mb-3" />
              <h4 className="text-label font-semibold text-foreground">Analyzing codebase…</h4>
              <p className="text-meta text-muted-foreground">Generating questions and model answers</p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Stats bar */}
              <div className="bg-surface border border-border rounded-xl p-5 space-y-3">
                <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-3 pb-3 border-b border-border">
                  <div>
                    <span className="text-meta font-medium text-primary uppercase tracking-wider">Study sheet</span>
                    <h3 className="text-label font-semibold text-foreground mt-0.5">{totalQuestions} questions</h3>
                  </div>
                  <div className="flex items-center gap-2 no-print">
                    <button onClick={handleLoadQuestionBank} disabled={isGeneratingBank} className="px-2.5 py-1.5 rounded-md bg-muted-surface border border-border text-meta font-medium text-foreground hover:bg-border transition-colors flex items-center gap-1.5">
                      <RefreshCw className={cn('w-3 h-3', isGeneratingBank && 'animate-spin')} /> Refresh
                    </button>
                    <button onClick={handleExportMarkdown} className="px-2.5 py-1.5 rounded-md bg-muted-surface border border-border text-meta font-medium text-foreground hover:bg-border transition-colors flex items-center gap-1.5">
                      <Download className="w-3 h-3" /> Export
                    </button>
                    <button onClick={() => window.print()} className="px-2.5 py-1.5 rounded-md bg-primary text-primary-foreground text-meta font-medium hover:opacity-90 transition-opacity flex items-center gap-1.5">
                      <Printer className="w-3 h-3" /> Print
                    </button>
                  </div>
                </div>

                {/* Progress */}
                <div className="space-y-1.5 no-print">
                  <div className="flex items-center justify-between text-meta font-medium">
                    <span className="text-foreground">{progressPercent}% practiced</span>
                    <div className="flex items-center gap-3 text-muted-foreground">
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-success inline-block" /> {practicedCount}</span>
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-warning inline-block" /> {shakyCount}</span>
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-border inline-block" /> {totalQuestions - practicedCount - shakyCount}</span>
                    </div>
                  </div>
                  <div className="w-full h-1.5 rounded-full bg-muted-surface overflow-hidden flex">
                    <div className="h-full bg-success transition-all" style={{ width: `${(practicedCount / totalQuestions) * 100}%` }} />
                    <div className="h-full bg-warning transition-all" style={{ width: `${(shakyCount / totalQuestions) * 100}%` }} />
                  </div>
                </div>
              </div>

              {/* Filters */}
              <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3 no-print">
                <div className="flex items-center gap-1 overflow-x-auto pb-1">
                  <button
                    onClick={() => setCategoryFilter('All')}
                    className={cn('px-2.5 py-1 rounded-md text-meta font-medium whitespace-nowrap transition-colors', categoryFilter === 'All' ? 'bg-primary text-primary-foreground' : 'bg-muted-surface text-muted-foreground hover:text-foreground')}
                  >
                    All ({totalQuestions})
                  </button>
                  {(questionBank.categories || []).map((cat: string) => {
                    const count = questionBank.questions.filter((q: any) => q.category === cat).length;
                    return (
                      <button
                        key={cat}
                        onClick={() => setCategoryFilter(cat)}
                        className={cn('px-2.5 py-1 rounded-md text-meta font-medium whitespace-nowrap transition-colors border', categoryFilter === cat ? 'bg-foreground text-canvas border-foreground' : `${CATEGORY_COLORS[cat] || 'bg-muted-surface text-muted-foreground border-border'}`)}
                      >
                        {cat} ({count})
                      </button>
                    );
                  })}
                </div>
                <div className="relative min-w-[200px]">
                  <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-muted-foreground" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search questions…"
                    className="w-full pl-8 pr-3 py-1.5 rounded-md bg-muted-surface border border-border text-meta text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
              </div>

              {/* Questions */}
              <div className="space-y-3">
                {filteredQuestions.length === 0 ? (
                  <div className="py-12 text-center bg-surface border border-border rounded-xl text-meta text-muted-foreground">
                    No questions match the current filter.
                  </div>
                ) : filteredQuestions.map((item: any, idx: number) => {
                  const status = questionStatuses[item.id] || 'unmarked';
                  return (
                    <div key={item.id || idx} className={cn(
                      'bg-surface border rounded-xl p-5 space-y-3 transition-colors',
                      status === 'practiced' ? 'border-success/30' : status === 'shaky' ? 'border-warning/30' : 'border-border'
                    )}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className={cn('px-2 py-0.5 rounded text-[11px] font-medium border', CATEGORY_COLORS[item.category] || 'bg-muted-surface text-muted-foreground border-border')}>
                            {item.category}
                          </span>
                          <span className="px-1.5 py-0.5 rounded bg-muted-surface text-meta text-muted-foreground">
                            {item.difficulty || 'Medium'}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 no-print">
                          <button
                            onClick={() => updateQuestionStatus(item.id, status === 'practiced' ? 'unmarked' : 'practiced')}
                            className={cn('px-2 py-1 rounded-md text-meta font-medium flex items-center gap-1 transition-colors', status === 'practiced' ? 'bg-success text-white' : 'bg-muted-surface text-muted-foreground hover:text-success')}
                          >
                            <CheckCircle2 className="w-3 h-3" />
                            {status === 'practiced' ? 'Practiced' : 'Mark done'}
                          </button>
                          <button
                            onClick={() => updateQuestionStatus(item.id, status === 'shaky' ? 'unmarked' : 'shaky')}
                            className={cn('px-2 py-1 rounded-md text-meta font-medium flex items-center gap-1 transition-colors', status === 'shaky' ? 'bg-warning text-white' : 'bg-muted-surface text-muted-foreground hover:text-warning')}
                          >
                            <AlertCircle className="w-3 h-3" />
                            {status === 'shaky' ? 'Shaky' : 'Mark shaky'}
                          </button>
                        </div>
                      </div>

                      <h4 className="text-body font-semibold text-foreground leading-snug">"{item.question}"</h4>

                      <div className="p-4 rounded-lg bg-muted-surface border border-border space-y-2">
                        <div className="flex items-center gap-1.5 text-meta font-medium text-primary">
                          <Sparkles className="w-3.5 h-3.5" />
                          Model answer
                        </div>
                        <p className="text-meta text-foreground leading-relaxed">"{item.model_answer}"</p>
                        {item.files_referenced?.length > 0 && (
                          <div className="pt-2 flex flex-wrap items-center gap-1.5 border-t border-border">
                            <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">Files:</span>
                            {item.files_referenced.map((fp: string, fi: number) => (
                              <span key={fi} className="px-1.5 py-0.5 rounded bg-surface border border-border text-[11px] font-mono text-muted-foreground flex items-center gap-1">
                                <FileCode className="w-2.5 h-2.5" /> {fp}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══ LIVE TAB ═══ */}
      {activeTab === 'live' && (
        <div className="space-y-4">
          <div className="bg-surface border border-border rounded-xl p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
            <div>
              <h3 className="text-label font-semibold text-foreground flex items-center gap-2">
                <Mic className="w-4 h-4 text-primary" />
                Mock viva
              </h3>
              <p className="text-meta text-muted-foreground">Answer questions about {activeRepo.name}.</p>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={difficulty}
                onChange={(e) => setDifficulty(e.target.value as 'easy' | 'medium' | 'hard')}
                className="px-2.5 py-1.5 rounded-md bg-muted-surface border border-border text-meta font-medium text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="easy">Foundation</option>
                <option value="medium">Intermediate</option>
                <option value="hard">Senior</option>
              </select>
              <button
                onClick={handleNextQuestion}
                disabled={isGeneratingQ}
                className="px-4 py-2 rounded-md bg-primary text-primary-foreground font-medium text-label hover:opacity-90 disabled:opacity-50 flex items-center gap-2 transition-opacity"
              >
                {isGeneratingQ ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {currentQuestion ? 'Next question' : 'Start'}
              </button>
            </div>
          </div>

          {summary ? (
            <div className="bg-surface border border-border rounded-xl p-6 space-y-5">
              <div className="flex items-center justify-between pb-4 border-b border-border">
                <div>
                  <span className="text-meta font-medium text-primary uppercase tracking-wider">Session report</span>
                  <h3 className="text-title text-foreground mt-0.5">{summary.grade || 'Complete'}</h3>
                </div>
                <div className="flex items-center gap-3 px-4 py-2 rounded-lg bg-selected border border-primary/20">
                  <Award className="w-6 h-6 text-primary" />
                  <div>
                    <div className="text-title font-bold text-foreground">{summary.overall_score}/10</div>
                    <div className="text-meta text-muted-foreground">Overall</div>
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="p-4 rounded-lg bg-success/5 border border-success/20 space-y-2">
                  <h4 className="text-label font-medium text-success flex items-center gap-1.5"><CheckCircle2 className="w-4 h-4" /> Strengths</h4>
                  <ul className="space-y-1 text-meta text-foreground">{summary.strengths?.map((s: string, i: number) => <li key={i}>• {s}</li>)}</ul>
                </div>
                <div className="p-4 rounded-lg bg-warning/5 border border-warning/20 space-y-2">
                  <h4 className="text-label font-medium text-warning flex items-center gap-1.5"><AlertCircle className="w-4 h-4" /> Areas to improve</h4>
                  <ul className="space-y-1 text-meta text-foreground">{summary.improvements?.map((s: string, i: number) => <li key={i}>• {s}</li>)}</ul>
                </div>
              </div>
              <div className="flex justify-end">
                <button
                  onClick={() => { setSummary(null); setSessionHistory([]); setCurrentQuestion(null); setEvaluation(null); }}
                  className="px-3 py-2 rounded-md bg-muted-surface text-label text-foreground font-medium hover:bg-border transition-colors flex items-center gap-1.5"
                >
                  <RotateCcw className="w-3.5 h-3.5" /> New session
                </button>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
              <div className="lg:col-span-7 space-y-4">
                {currentQuestion ? (
                  <div className="bg-surface border border-border rounded-xl p-5 space-y-4">
                    <div className="p-4 rounded-lg bg-muted-surface border border-border space-y-2">
                      <div className="flex items-center justify-between text-meta">
                        <span className="font-medium text-primary flex items-center gap-1.5">
                          <BookOpen className="w-3.5 h-3.5" /> {currentQuestion.topic || 'General'}
                        </span>
                        <span className="px-1.5 py-0.5 rounded bg-surface border border-border text-muted-foreground">{currentQuestion.difficulty}</span>
                      </div>
                      <h3 className="text-body font-semibold text-foreground leading-snug">"{currentQuestion.question}"</h3>
                      {currentQuestion.context_file && (
                        <span className="text-meta font-mono text-muted-foreground flex items-center gap-1">
                          <FileCode className="w-3 h-3" /> {currentQuestion.context_file}
                        </span>
                      )}
                    </div>
                    <VoiceRecorder
                      onSpeechRecorded={(text) => handleAnswerSubmit(text)}
                      questionToSpeak={currentQuestion.question}
                      isEvaluating={isEvaluating}
                    />
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center py-16 bg-surface border border-border rounded-xl text-center">
                    <Mic className="w-10 h-10 text-muted-foreground/30 mb-3" />
                    <h3 className="text-label font-semibold text-foreground">Ready for practice?</h3>
                    <p className="text-meta text-muted-foreground max-w-xs mt-1">
                      Click "Start" to get your first question about {activeRepo.name}.
                    </p>
                  </div>
                )}
              </div>

              <div className="lg:col-span-5 space-y-4">
                {evaluation && (
                  <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
                    <div className="bg-surface border-2 border-primary/30 rounded-xl p-5 space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-meta font-medium text-muted-foreground uppercase tracking-wider">Evaluation</span>
                        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-selected text-foreground font-bold text-label">
                          <Award className="w-4 h-4 text-primary" />
                          {evaluation.score}/10
                        </div>
                      </div>
                      <div className="p-2.5 rounded-md bg-muted-surface text-meta font-medium text-foreground">
                        Verdict: <span className="text-primary font-semibold">{evaluation.verdict}</span>
                      </div>
                      <div className="space-y-1 text-meta">
                        <span className="font-medium text-foreground">Critique:</span>
                        <p className="text-muted-foreground leading-relaxed bg-muted-surface p-3 rounded-md border border-border">{evaluation.critique}</p>
                      </div>
                      <div className="space-y-1 text-meta">
                        <span className="font-medium text-foreground flex items-center gap-1"><Sparkles className="w-3 h-3 text-warning" /> Ideal answer:</span>
                        <p className="text-muted-foreground italic bg-muted-surface p-3 rounded-md border border-border">"{evaluation.ideal_answer}"</p>
                      </div>
                      <div className="pt-2 border-t border-border flex justify-end">
                        <button
                          onClick={handleNextQuestion}
                          disabled={isGeneratingQ}
                          className="px-3.5 py-1.5 rounded-md bg-primary text-primary-foreground font-medium text-meta hover:opacity-90 disabled:opacity-50 flex items-center gap-1.5 transition-opacity"
                        >
                          {isGeneratingQ ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                          Next question
                        </button>
                      </div>
                    </div>
                  </motion.div>
                )}

                <div className="bg-surface border border-border rounded-xl p-4 space-y-3">
                  <div className="flex items-center justify-between pb-2 border-b border-border">
                    <span className="text-meta font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="w-3.5 h-3.5" /> Session ({sessionHistory.length} answered)
                    </span>
                    {sessionHistory.length > 0 && (
                      <button
                        onClick={handleFinishSession}
                        disabled={isGeneratingSummary}
                        className="px-2.5 py-1 rounded-md bg-primary text-primary-foreground text-meta font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-1.5 transition-opacity"
                      >
                        {isGeneratingSummary ? <Loader2 className="w-3 h-3 animate-spin" /> : <Award className="w-3 h-3" />}
                        Finish
                      </button>
                    )}
                  </div>
                  {sessionHistory.length === 0 ? (
                    <p className="py-4 text-center text-meta text-muted-foreground">No answers yet.</p>
                  ) : (
                    <div className="space-y-2">
                      {sessionHistory.map((entry, idx) => (
                        <div key={idx} className="p-2.5 rounded-md bg-muted-surface text-meta flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="font-medium text-foreground truncate">Q{idx + 1}: {entry.question}</p>
                            <p className="text-muted-foreground">{entry.verdict}</p>
                          </div>
                          <span className={cn('px-1.5 py-0.5 rounded font-bold text-[11px] shrink-0',
                            (entry.score >= 7) ? 'bg-success/10 text-success' :
                            (entry.score >= 4) ? 'bg-warning/10 text-warning' :
                            'bg-danger/10 text-danger'
                          )}>
                            {entry.score}/10
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
