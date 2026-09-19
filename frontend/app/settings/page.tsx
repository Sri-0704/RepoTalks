'use client';

import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import {
  Settings,
  KeyRound,
  Eye,
  EyeOff,
  CheckCircle2,
  AlertCircle,
  ShieldCheck,
  Loader2,
  Sun,
  Moon,
  Laptop,
  Palette,
  Trash2,
  Cpu,
  Zap,
  Sparkles,
  Info,
  ShieldAlert,
} from 'lucide-react';
import { testGeminiKey, testGroqKey } from '@/lib/api';
import {
  getGeminiApiKey,
  setGeminiApiKey,
  removeGeminiApiKey,
  getGroqApiKey,
  setGroqApiKey,
  removeGroqApiKey,
  getLLMProvider,
  setLLMProvider,
  LLMProviderPreference,
} from '@/lib/storage';
import { useTheme, Theme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();

  // Gemini state
  const [geminiKey, setGeminiKey] = useState('');
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [geminiTestResult, setGeminiTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [geminiTesting, setGeminiTesting] = useState(false);
  const [geminiSaved, setGeminiSaved] = useState(false);

  // Groq state
  const [groqKey, setGroqKey] = useState('');
  const [showGroqKey, setShowGroqKey] = useState(false);
  const [groqTestResult, setGroqTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [groqTesting, setGroqTesting] = useState(false);
  const [groqSaved, setGroqSaved] = useState(false);

  // Provider preference state
  const [providerPref, setProviderPref] = useState<LLMProviderPreference>('auto');
  const [providerSaved, setProviderSaved] = useState(false);

  useEffect(() => {
    setGeminiKey(getGeminiApiKey());
    setGroqKey(getGroqApiKey());
    setProviderPref(getLLMProvider());
  }, []);

  // Gemini Key Handlers
  const handleSaveGemini = () => {
    setGeminiApiKey(geminiKey);
    setGeminiTestResult(null);
    setGeminiSaved(true);
    setTimeout(() => setGeminiSaved(false), 2000);
  };

  const handleRemoveGemini = () => {
    setGeminiKey('');
    removeGeminiApiKey();
    setGeminiTestResult(null);
    setGeminiSaved(false);
  };

  const handleTestGemini = async () => {
    if (!geminiKey.trim()) {
      setGeminiTestResult({ success: false, message: 'Enter a Gemini API key before testing.' });
      return;
    }
    setGeminiTesting(true);
    setGeminiTestResult(null);
    try {
      const res = await testGeminiKey(geminiKey.trim());
      setGeminiTestResult({
        success: res.success,
        message: res.message || 'Connection verified.',
      });
    } catch (err: any) {
      setGeminiTestResult({
        success: false,
        message: err.message || 'Connection test failed',
      });
    } finally {
      setGeminiTesting(false);
    }
  };

  // Groq Key Handlers
  const handleSaveGroq = () => {
    setGroqApiKey(groqKey);
    setGroqTestResult(null);
    setGroqSaved(true);
    setTimeout(() => setGroqSaved(false), 2000);
  };

  const handleRemoveGroq = () => {
    setGroqKey('');
    removeGroqApiKey();
    setGroqTestResult(null);
    setGroqSaved(false);
  };

  const handleTestGroq = async () => {
    if (!groqKey.trim()) {
      setGroqTestResult({ success: false, message: 'Enter a Groq API key before testing.' });
      return;
    }
    setGroqTesting(true);
    setGroqTestResult(null);
    try {
      const res = await testGroqKey(groqKey.trim());
      setGroqTestResult({
        success: res.success,
        message: res.message || 'Connection verified.',
      });
    } catch (err: any) {
      setGroqTestResult({
        success: false,
        message: err.message || 'Groq connection test failed',
      });
    } finally {
      setGroqTesting(false);
    }
  };

  // Provider Preference Handler
  const handleSelectProvider = (provider: LLMProviderPreference) => {
    setProviderPref(provider);
    setLLMProvider(provider);
    setProviderSaved(true);
    setTimeout(() => setProviderSaved(false), 2000);
  };

  const themeOptions: { value: Theme; label: string; desc: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { value: 'light', label: 'Light', desc: 'Clean, bright workspace', icon: Sun },
    { value: 'dark', label: 'Dark', desc: 'Reduced eye strain', icon: Moon },
    { value: 'system', label: 'System', desc: 'Follows your OS preference', icon: Laptop },
  ];

  const providerOptions: {
    value: LLMProviderPreference;
    label: string;
    badge: string;
    desc: string;
    icon: React.ComponentType<{ className?: string }>;
  }[] = [
    {
      value: 'auto',
      label: 'Automatic Failover',
      badge: 'Recommended',
      desc: 'Prefers Groq (openai/gpt-oss-120b) for fast generation; automatically falls back to Gemini if rate-limited or unavailable.',
      icon: Zap,
    },
    {
      value: 'groq',
      label: 'Groq Only',
      badge: 'Fastest',
      desc: 'Forces all text generation exclusively through Groq. Requires a valid Groq API key.',
      icon: Cpu,
    },
    {
      value: 'gemini',
      label: 'Gemini Only',
      badge: 'Multimodal',
      desc: 'Directs all generation exclusively to Google Gemini (3.6-flash / 3.5-flash). Groq is never invoked.',
      icon: Sparkles,
    },
  ];

  return (
    <div className="max-w-2xl mx-auto space-y-6 pb-12">
      {/* Header */}
      <div>
        <h2 className="text-title text-foreground flex items-center gap-2">
          <Settings className="w-5 h-5 text-primary" />
          Settings
        </h2>
        <p className="text-meta text-muted-foreground mt-0.5">Configure LLM providers, API keys, and appearance.</p>
      </div>

      {/* LLM Provider Selection */}
      <div className="bg-surface border border-border rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-border">
          <div className="flex items-center gap-2">
            <Cpu className="w-5 h-5 text-primary" />
            <h3 className="text-label font-semibold text-foreground">LLM Provider Mode</h3>
          </div>
          {providerSaved && (
            <span className="text-xs text-success flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" /> Saved
            </span>
          )}
        </div>

        <p className="text-meta text-muted-foreground">
          Choose which provider powers chat, architecture diagrams, code tracing, and viva evaluation.
          Repository embeddings and image analysis always use Gemini.
        </p>

        <div role="radiogroup" aria-label="LLM Provider Mode" className="space-y-3">
          {providerOptions.map((opt) => {
            const isSelected = providerPref === opt.value;
            const Icon = opt.icon;
            return (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={isSelected}
                onClick={() => handleSelectProvider(opt.value)}
                className={cn(
                  'w-full p-4 rounded-lg border text-left transition-all flex items-start gap-3',
                  isSelected
                    ? 'bg-selected border-primary/40 ring-2 ring-ring ring-offset-1'
                    : 'bg-surface border-border hover:bg-muted-surface'
                )}
              >
                <div
                  className={cn(
                    'w-9 h-9 rounded-md flex items-center justify-center shrink-0 mt-0.5',
                    isSelected ? 'bg-primary text-primary-foreground' : 'bg-muted-surface text-muted-foreground'
                  )}
                >
                  <Icon className="w-5 h-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <h4 className="text-label font-medium text-foreground">{opt.label}</h4>
                    <span
                      className={cn(
                        'text-[10px] uppercase font-semibold px-2 py-0.5 rounded-full',
                        isSelected ? 'bg-primary/15 text-primary' : 'bg-muted-surface text-muted-foreground'
                      )}
                    >
                      {opt.badge}
                    </span>
                  </div>
                  <p className="text-meta text-muted-foreground mt-1 leading-relaxed">{opt.desc}</p>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Groq API Key */}
      <div className="bg-surface border border-border rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-border">
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-amber-500" />
            <h3 className="text-label font-semibold text-foreground">Groq API Key</h3>
          </div>
          <a
            href="https://console.groq.com/keys"
            target="_blank"
            rel="noreferrer"
            className="text-meta text-primary hover:underline"
          >
            Get a free Groq key →
          </a>
        </div>

        <p className="text-meta text-muted-foreground">
          Powers high-speed text generation with openai/gpt-oss-120b.
        </p>

        <div className="space-y-3">
          <label htmlFor="groq-api-key" className="text-label font-medium text-foreground block">
            API Key
          </label>
          <div className="relative">
            <input
              id="groq-api-key"
              type={showGroqKey ? 'text' : 'password'}
              placeholder="gsk_…"
              value={groqKey}
              onChange={(e) => {
                setGroqKey(e.target.value);
                setGroqSaved(false);
                setGroqTestResult(null);
              }}
              className="w-full px-3 pr-10 py-2.5 rounded-md bg-muted-surface border border-border text-body font-mono text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <button
              type="button"
              onClick={() => setShowGroqKey(!showGroqKey)}
              className="absolute right-2.5 top-2.5 p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
              aria-label={showGroqKey ? 'Hide key' : 'Show key'}
            >
              {showGroqKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSaveGroq}
                disabled={!groqKey.trim()}
                className="px-3 py-2 rounded-md bg-primary text-primary-foreground font-medium text-label hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5"
              >
                {groqSaved ? <CheckCircle2 className="w-3.5 h-3.5" /> : <KeyRound className="w-3.5 h-3.5" />}
                {groqSaved ? 'Saved' : 'Save'}
              </button>
              <button
                type="button"
                onClick={handleTestGroq}
                disabled={groqTesting || !groqKey.trim()}
                className="px-3 py-2 rounded-md bg-muted-surface border border-border text-label font-medium text-foreground hover:bg-border disabled:opacity-50 transition-colors flex items-center gap-1.5"
              >
                {groqTesting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                Test connection
              </button>
              {groqKey.trim() && (
                <button
                  type="button"
                  onClick={handleRemoveGroq}
                  className="px-3 py-2 rounded-md text-label text-danger hover:bg-danger/5 transition-colors flex items-center gap-1.5"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Remove
                </button>
              )}
            </div>
          </div>

          {/* Test result */}
          {groqTestResult && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              className={cn(
                'p-3 rounded-lg border text-meta',
                groqTestResult.success
                  ? 'bg-success/5 border-success/20 text-success'
                  : 'bg-danger/5 border-danger/20 text-danger'
              )}
            >
              <div className="font-medium flex items-center gap-1.5">
                {groqTestResult.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                {groqTestResult.success ? 'Groq connection valid' : 'Groq connection failed'}
              </div>
              <p className="mt-0.5">{groqTestResult.message}</p>
            </motion.div>
          )}
        </div>
      </div>

      {/* Gemini API Key */}
      <div className="bg-surface border border-border rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-border">
          <div className="flex items-center gap-2">
            <KeyRound className="w-5 h-5 text-primary" />
            <h3 className="text-label font-semibold text-foreground">Gemini API Key</h3>
          </div>
          <a
            href="https://aistudio.google.com/app/apikey"
            target="_blank"
            rel="noreferrer"
            className="text-meta text-primary hover:underline"
          >
            Get a free Gemini key →
          </a>
        </div>

        <p className="text-meta text-muted-foreground">
          Required for repository indexing, vector embeddings (text-embedding-004), and image/multimodal analysis.
        </p>

        <div className="space-y-3">
          <label htmlFor="gemini-api-key" className="text-label font-medium text-foreground block">
            API Key
          </label>
          <div className="relative">
            <input
              id="gemini-api-key"
              type={showGeminiKey ? 'text' : 'password'}
              placeholder="Paste your Gemini API key…"
              value={geminiKey}
              onChange={(e) => {
                setGeminiKey(e.target.value);
                setGeminiSaved(false);
                setGeminiTestResult(null);
              }}
              className="w-full px-3 pr-10 py-2.5 rounded-md bg-muted-surface border border-border text-body font-mono text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <button
              type="button"
              onClick={() => setShowGeminiKey(!showGeminiKey)}
              className="absolute right-2.5 top-2.5 p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
              aria-label={showGeminiKey ? 'Hide key' : 'Show key'}
            >
              {showGeminiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSaveGemini}
                disabled={!geminiKey.trim()}
                className="px-3 py-2 rounded-md bg-primary text-primary-foreground font-medium text-label hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5"
              >
                {geminiSaved ? <CheckCircle2 className="w-3.5 h-3.5" /> : <KeyRound className="w-3.5 h-3.5" />}
                {geminiSaved ? 'Saved' : 'Save'}
              </button>
              <button
                type="button"
                onClick={handleTestGemini}
                disabled={geminiTesting || !geminiKey.trim()}
                className="px-3 py-2 rounded-md bg-muted-surface border border-border text-label font-medium text-foreground hover:bg-border disabled:opacity-50 transition-colors flex items-center gap-1.5"
              >
                {geminiTesting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                Test connection
              </button>
              {geminiKey.trim() && (
                <button
                  type="button"
                  onClick={handleRemoveGemini}
                  className="px-3 py-2 rounded-md text-label text-danger hover:bg-danger/5 transition-colors flex items-center gap-1.5"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Remove
                </button>
              )}
            </div>
          </div>

          {/* Test result */}
          {geminiTestResult && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              className={cn(
                'p-3 rounded-lg border text-meta',
                geminiTestResult.success
                  ? 'bg-success/5 border-success/20 text-success'
                  : 'bg-danger/5 border-danger/20 text-danger'
              )}
            >
              <div className="font-medium flex items-center gap-1.5">
                {geminiTestResult.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                {geminiTestResult.success ? 'Gemini connection valid' : 'Gemini connection failed'}
              </div>
              <p className="mt-0.5">{geminiTestResult.message}</p>
            </motion.div>
          )}
        </div>
      </div>

      {/* Credential Security & Privacy Notice */}
      <div className="bg-muted-surface/50 border border-border rounded-xl p-5 space-y-3">
        <div className="flex items-center gap-2 text-foreground font-medium text-label">
          <ShieldAlert className="w-4 h-4 text-primary" />
          <span>Credential Storage & Privacy</span>
        </div>
        <div className="space-y-2 text-meta text-muted-foreground leading-relaxed">
          <p>
            <strong>Convenience storage only:</strong> Keys entered here are stored in your browser&apos;s{' '}
            <code className="bg-surface px-1 py-0.5 rounded border border-border text-foreground font-mono">
              localStorage
            </code>{' '}
            for session convenience. Browser localStorage is not secure encrypted credential storage.
          </p>
          <p>
            <strong>Request-scoped transmission:</strong> Keys are forwarded as request-scoped headers (
            <code className="bg-surface px-1 py-0.5 rounded border border-border text-foreground font-mono">
              X-Groq-API-Key
            </code>
            ,{' '}
            <code className="bg-surface px-1 py-0.5 rounded border border-border text-foreground font-mono">
              X-Gemini-API-Key
            </code>
            ) only when invoking LLM features. They are never sent on non-LLM operations like project listing or local search.
          </p>
          <p>
            <strong>Zero backend persistence:</strong> Your keys are never stored in SQLite, server files, job logs, caches, error dumps, or telemetry.
          </p>
        </div>
      </div>

      {/* Appearance */}
      <div className="bg-surface border border-border rounded-xl p-6 space-y-4">
        <div className="flex items-center gap-2 pb-3 border-b border-border">
          <Palette className="w-5 h-5 text-primary" />
          <h3 className="text-label font-semibold text-foreground">Appearance</h3>
        </div>

        <div role="radiogroup" aria-label="Theme selection" className="grid grid-cols-3 gap-3">
          {themeOptions.map((opt) => {
            const isSelected = theme === opt.value;
            const Icon = opt.icon;
            return (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={isSelected}
                onClick={() => setTheme(opt.value)}
                className={cn(
                  'p-4 rounded-lg border text-left transition-colors',
                  isSelected
                    ? 'bg-selected border-primary/30 ring-2 ring-ring ring-offset-1'
                    : 'bg-surface border-border hover:bg-muted-surface'
                )}
              >
                <div
                  className={cn(
                    'w-8 h-8 rounded-md flex items-center justify-center mb-2',
                    isSelected ? 'bg-primary text-primary-foreground' : 'bg-muted-surface text-muted-foreground'
                  )}
                >
                  <Icon className="w-4 h-4" />
                </div>
                <h4 className="text-label font-medium text-foreground">{opt.label}</h4>
                <p className="text-meta text-muted-foreground mt-0.5">{opt.desc}</p>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
