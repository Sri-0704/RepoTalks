'use client';

import React, { useState, useEffect, useRef } from 'react';
import dynamic from 'next/dynamic';
import {
  Network,
  Layers,
  Cpu,
  Workflow,
  Database,
  RefreshCw,
  Play,
  AlertCircle,
  Loader2,
} from 'lucide-react';
import { fetchArchitectureMap, DiagramType, ArchitectureResponse } from '@/lib/api';
import { useProject } from '@/context/ProjectContext';
import { cn } from '@/lib/utils';

const InteractiveMindMap = dynamic(
  () => import('@/components/InteractiveMindMap').then((mod) => mod.InteractiveMindMap),
  {
    ssr: false,
    loading: () => (
      <div className="w-full h-[600px] flex items-center justify-center rounded-lg bg-muted-surface border border-border">
        <div className="flex items-center gap-2 text-meta text-muted-foreground">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>Loading canvas…</span>
        </div>
      </div>
    ),
  }
);

const VIEW_TYPES: { id: DiagramType; label: string; icon: any }[] = [
  { id: 'component_tree', label: 'Components', icon: Layers },
  { id: 'api_flow', label: 'API flows', icon: Cpu },
  { id: 'data_flow', label: 'Data flow', icon: Workflow },
  { id: 'db_schema', label: 'Database', icon: Database },
];

export default function ArchitecturePage() {
  const { activeRepo } = useProject();
  const [diagramType, setDiagramType] = useState<DiagramType>('component_tree');
  const [architectureData, setArchitectureData] = useState<ArchitectureResponse | null>(null);
  const [diagramCache, setDiagramCache] = useState<Record<string, ArchitectureResponse>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRequested, setHasRequested] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef<number>(0);
  const [showDbTab, setShowDbTab] = useState(true);

  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);

  useEffect(() => {
    setArchitectureData(null);
    setDiagramCache({});
    setHasRequested(false);
    setError(null);
    if (activeRepo) {
      const files = activeRepo.files || [];
      const dbIndicators = ['.sql', 'model', 'schema', 'db', 'entity', 'prisma', 'migration'];
      const hasDb = files.some((f: any) =>
        dbIndicators.some((ind) => (f.path || '').toLowerCase().includes(ind)) || f.extension === '.sql'
      );
      setShowDbTab(hasDb);
    }
  }, [activeRepo?.repo_id]);

  const loadDiagram = async (repoId: string, type: DiagramType, bypassCache = false) => {
    const cacheKey = `${repoId}:${type}`;
    if (!bypassCache && diagramCache[cacheKey]) {
      setArchitectureData(diagramCache[cacheKey]);
      setHasRequested(true);
      setError(null);
      return;
    }

    requestIdRef.current += 1;
    const currentRequestId = requestIdRef.current;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setHasRequested(true);
    setError(null);
    setArchitectureData(null);

    try {
      const data = await fetchArchitectureMap(repoId, type, bypassCache, controller.signal);
      if (currentRequestId === requestIdRef.current) {
        setArchitectureData(data);
        setDiagramCache((prev) => ({ ...prev, [cacheKey]: data }));
        if (type === 'db_schema' && data.has_data === false) {
          setShowDbTab(false);
        }
      }
    } catch (err: any) {
      if (currentRequestId === requestIdRef.current && err.name !== 'AbortError') {
        setError(err.message || 'Failed to generate architecture map');
        setArchitectureData(null);
      }
    } finally {
      if (currentRequestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  };

  const handleTabChange = (type: DiagramType) => {
    setDiagramType(type);
    if (activeRepo) loadDiagram(activeRepo.repo_id, type);
  };

  if (!activeRepo) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] text-center">
        <Network className="w-10 h-10 text-muted-foreground/30 mb-3" />
        <h3 className="text-label font-semibold text-foreground">No repository selected</h3>
        <p className="text-meta text-muted-foreground mt-1">Add or select a repository first.</p>
      </div>
    );
  }

  const visibleTabs = VIEW_TYPES.filter((t) => t.id !== 'db_schema' || showDbTab);

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-3 bg-surface border border-border rounded-xl p-4">
        <div>
          <h2 className="text-section text-foreground flex items-center gap-2">
            <Network className="w-5 h-5 text-primary" />
            Architecture map
          </h2>
          <p className="text-meta text-muted-foreground mt-0.5">
            Interactive mind map canvas — select a view and generate.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* View selector */}
          <div className="flex items-center gap-1 p-0.5 rounded-md bg-muted-surface border border-border">
            {visibleTabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = diagramType === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => handleTabChange(tab.id)}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1.5 rounded text-meta font-medium transition-colors',
                    isActive
                      ? 'bg-primary text-primary-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  <Icon className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">{tab.label}</span>
                </button>
              );
            })}
          </div>

          {/* Refresh button */}
          {hasRequested && (
            <button
              onClick={() => activeRepo && loadDiagram(activeRepo.repo_id, diagramType, true)}
              disabled={loading}
              className="p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted-surface border border-border transition-colors disabled:opacity-50"
              aria-label="Regenerate"
              title="Bypass cache and regenerate"
            >
              <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
            </button>
          )}
        </div>
      </div>

      {/* Canvas area */}
      {!hasRequested ? (
        <div className="flex flex-col items-center justify-center py-24 bg-surface border border-border rounded-xl text-center">
          <Network className="w-12 h-12 text-muted-foreground/30 mb-4" />
          <h3 className="text-label font-semibold text-foreground">Generate a {diagramType.replace(/_/g, ' ')} map</h3>
          <p className="text-meta text-muted-foreground max-w-sm mt-1">
            Produces an interactive mind map for {activeRepo.name}.
          </p>
          <button
            onClick={() => loadDiagram(activeRepo.repo_id, diagramType)}
            className="mt-4 px-4 py-2 rounded-md bg-primary text-primary-foreground font-medium text-label hover:opacity-90 transition-opacity flex items-center gap-2"
          >
            <Play className="w-4 h-4" />
            Generate
          </button>
        </div>
      ) : loading && !architectureData ? (
        <div className="flex flex-col items-center justify-center py-24 bg-surface border border-border rounded-xl">
          <Loader2 className="w-8 h-8 animate-spin text-primary mb-3" />
          <h4 className="text-label font-semibold text-foreground">Analyzing architecture…</h4>
          <p className="text-meta text-muted-foreground">Parsing structure and dependencies</p>
        </div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center py-16 bg-surface border border-border rounded-xl text-center">
          <AlertCircle className="w-8 h-8 text-danger mb-3" />
          <h4 className="text-label font-semibold text-foreground">Analysis failed</h4>
          <p className="text-meta text-muted-foreground max-w-sm mt-1">{error}</p>
          <button
            onClick={() => loadDiagram(activeRepo.repo_id, diagramType, true)}
            className="mt-3 px-3 py-1.5 rounded-md bg-muted-surface text-label text-foreground hover:bg-border transition-colors flex items-center gap-1.5"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Retry
          </button>
        </div>
      ) : architectureData?.has_data === false ? (
        <div className="flex flex-col items-center justify-center py-16 bg-surface border border-border rounded-xl text-center">
          <AlertCircle className="w-8 h-8 text-warning mb-3" />
          <h4 className="text-label font-semibold text-foreground">No {diagramType.replace(/_/g, ' ')} data</h4>
          <p className="text-meta text-muted-foreground max-w-sm mt-1">
            {architectureData.reason || 'This repository does not contain matching structure.'}
          </p>
        </div>
      ) : (
        <div className="relative">
          {loading && (
            <div className="absolute top-3 right-3 z-10 flex items-center gap-2 px-3 py-1.5 rounded-md bg-surface border border-border shadow-sm text-meta text-muted-foreground">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Updating…
            </div>
          )}
          <InteractiveMindMap
            data={architectureData}
            onRefresh={() => loadDiagram(activeRepo.repo_id, diagramType, true)}
          />
        </div>
      )}
    </div>
  );
}
