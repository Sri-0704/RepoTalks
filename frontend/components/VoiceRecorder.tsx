'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, MicOff, Volume2, VolumeX, Send, AlertCircle, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

interface VoiceRecorderProps {
  onSpeechRecorded: (text: string) => void;
  questionToSpeak?: string;
  isEvaluating?: boolean;
}

export const VoiceRecorder: React.FC<VoiceRecorderProps> = ({
  onSpeechRecorded,
  questionToSpeak,
  isEvaluating = false,
}) => {
  const [hasSpeechSupport, setHasSpeechSupport] = useState(true);
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [errorNotice, setErrorNotice] = useState<string | null>(null);
  const [transcript, setTranscript] = useState('');
  const [manualInput, setManualInput] = useState('');
  const [ttsEnabled, setTtsEnabled] = useState(true);

  const recognitionRef = useRef<any>(null);
  const baseInputRef = useRef<string>('');
  const manualInputRef = useRef<string>('');

  useEffect(() => {
    manualInputRef.current = manualInput;
  }, [manualInput]);

  // Initialize Speech Recognition
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const SpeechRecognition =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

      if (!SpeechRecognition) {
        setHasSpeechSupport(false);
        return;
      }

      try {
        const recognition = new SpeechRecognition();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = 'en-US';

        recognition.onresult = (event: any) => {
          let currentTranscript = '';
          for (let i = 0; i < event.results.length; i++) {
            currentTranscript += event.results[i][0].transcript;
          }
          setTranscript(currentTranscript);
          const prefix = baseInputRef.current.trim() ? baseInputRef.current.trim() + ' ' : '';
          setManualInput(prefix + currentTranscript);
        };

        recognition.onerror = (event: any) => {
          setIsListening(false);
          if (event.error === 'not-allowed') {
            setErrorNotice('Microphone access was denied. Please allow microphone permissions or type your answer.');
          } else if (event.error !== 'aborted') {
            setErrorNotice(`Speech recognition error: ${event.error}`);
          }
        };

        recognition.onend = () => {
          setIsListening(false);
        };

        recognitionRef.current = recognition;
      } catch {
        setHasSpeechSupport(false);
      }

      return () => {
        try { recognitionRef.current?.stop(); } catch {}
        try { window.speechSynthesis?.cancel(); } catch {}
      };
    }
  }, []);

  // Text to Speech playback for Examiner Questions
  useEffect(() => {
    if (questionToSpeak && ttsEnabled && typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(questionToSpeak);
      utterance.rate = 1.0;
      utterance.pitch = 1.0;

      utterance.onstart = () => setIsSpeaking(true);
      utterance.onend = () => setIsSpeaking(false);
      utterance.onerror = () => setIsSpeaking(false);

      window.speechSynthesis.speak(utterance);
      return () => {
        try { window.speechSynthesis.cancel(); } catch {}
      };
    }
  }, [questionToSpeak, ttsEnabled]);

  const toggleListening = useCallback(() => {
    setErrorNotice(null);
    if (!recognitionRef.current) {
      setErrorNotice('Speech recognition is not supported in this browser. Please type your answer below.');
      return;
    }

    if (isListening) {
      try { recognitionRef.current.stop(); } catch {}
      setIsListening(false);
    } else {
      try {
        baseInputRef.current = manualInputRef.current;
        recognitionRef.current.start();
        setIsListening(true);
      } catch (err: any) {
        setErrorNotice('Could not start microphone recording. Please try typing instead.');
      }
    }
  }, [isListening]);

  const handleSendResponse = () => {
    const textToSend = manualInput.trim() || transcript.trim();
    if (!textToSend || isEvaluating) return;

    if (isListening && recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch {}
      setIsListening(false);
    }

    onSpeechRecorded(textToSend);
  };

  const hasContent = !!(manualInput.trim() || transcript.trim());

  return (
    <div className="bg-surface border border-border rounded-xl p-5 space-y-4">
      {/* Unsupported or permission error notice */}
      {(!hasSpeechSupport || errorNotice) && (
        <div className="flex items-start gap-2 p-3 rounded-md bg-warning/10 border border-warning/20 text-meta text-warning">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <div className="flex-1">
            {errorNotice || 'Speech recognition is not available in this browser. You can type your answer below.'}
          </div>
          {errorNotice && (
            <button
              onClick={() => setErrorNotice(null)}
              className="text-muted-foreground hover:text-foreground text-xs"
            >
              Dismiss
            </button>
          )}
        </div>
      )}

      {/* Voice Controls Bar */}
      <div className="flex items-center justify-between flex-wrap gap-3 pb-3 border-b border-border">
        <div className="flex items-center gap-2">
          {hasSpeechSupport ? (
            <button
              type="button"
              onClick={toggleListening}
              disabled={isEvaluating}
              className={cn(
                'flex items-center gap-2 px-3 py-2 rounded-md font-medium text-meta transition-colors',
                isListening
                  ? 'bg-danger text-white hover:bg-danger/90 animate-pulse'
                  : 'bg-muted-surface hover:bg-border text-foreground'
              )}
              aria-label={isListening ? 'Stop recording voice' : 'Start recording voice'}
            >
              {isListening ? (
                <>
                  <MicOff className="w-4 h-4" />
                  <span>Recording… (Click to stop)</span>
                </>
              ) : (
                <>
                  <Mic className="w-4 h-4 text-primary" />
                  <span>Record voice answer</span>
                </>
              )}
            </button>
          ) : (
            <span className="text-meta text-muted-foreground">Type answer below</span>
          )}

          {isSpeaking && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-selected text-selected-foreground text-meta font-medium">
              <Volume2 className="w-3.5 h-3.5 animate-pulse" />
              Examiner reading…
            </span>
          )}
        </div>

        {/* TTS Toggle */}
        <button
          type="button"
          onClick={() => setTtsEnabled(!ttsEnabled)}
          className="flex items-center gap-1.5 text-meta text-muted-foreground hover:text-foreground transition-colors"
        >
          {ttsEnabled ? (
            <Volume2 className="w-3.5 h-3.5 text-primary" />
          ) : (
            <VolumeX className="w-3.5 h-3.5" />
          )}
          <span>Examiner audio: {ttsEnabled ? 'On' : 'Muted'}</span>
        </button>
      </div>

      {/* Answer Input Area (Accessible textarea with persistent draft) */}
      <div className="space-y-2">
        <label htmlFor="viva-answer-input" className="text-meta font-medium text-foreground block">
          Your Answer
        </label>
        <div className="relative">
          <textarea
            id="viva-answer-input"
            rows={4}
            value={manualInput}
            onChange={(e) => setManualInput(e.target.value)}
            disabled={isEvaluating}
            placeholder={
              isListening
                ? 'Listening to your speech… words will appear here.'
                : 'Type your explanation or record via microphone above…'
            }
            className="w-full px-3.5 py-3 rounded-lg bg-muted-surface border border-border text-body text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-y min-h-[96px]"
          />
        </div>
      </div>

      {/* Submission Actions */}
      <div className="flex items-center justify-between pt-1">
        <span className="text-[11px] text-muted-foreground">
          {manualInput.length > 0 ? `${manualInput.trim().split(/\s+/).filter(Boolean).length} words` : 'Draft will be preserved until evaluation finishes'}
        </span>

        <div className="flex items-center gap-2">
          {manualInput && !isEvaluating && (
            <button
              type="button"
              onClick={() => { setManualInput(''); setTranscript(''); }}
              className="px-3 py-1.5 rounded-md text-meta text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors"
            >
              Clear
            </button>
          )}

          <button
            type="button"
            onClick={handleSendResponse}
            disabled={!hasContent || isEvaluating}
            className="flex items-center gap-1.5 px-4 py-2 rounded-md bg-primary text-primary-foreground font-medium text-meta hover:opacity-90 disabled:opacity-40 transition-opacity"
          >
            {isEvaluating ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Evaluating…</span>
              </>
            ) : (
              <>
                <Send className="w-4 h-4" />
                <span>Submit answer</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
