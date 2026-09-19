'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
  Handle,
  Position,
  useReactFlow,
  ReactFlowProvider,
  Node,
  Edge,
  NodeProps,
  NodeTypes
} from '@xyflow/react';
import dagre from 'dagre';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  RefreshCw,
  Folder,
  FolderOpen,
  Code,
  Sparkles,
  X,
  Sliders,
  ChevronDown,
  ChevronRight
} from 'lucide-react';

interface MindNodeData extends Record<string, unknown> {
  id: string;
  label: string;
  type?: string;
  file?: string;
  dir?: string;
  description?: string;
  connected_to?: string[];
  data_passed?: string[];
  is_group?: boolean;
  child_count?: number;
  is_collapsed?: boolean;
  onToggleCollapse?: (dirId: string) => void;
  onSelectNode?: (nodeData: any) => void;
}

// --- Custom React Flow Node Component ---
const ArchitectureNode: React.FC<NodeProps<Node<MindNodeData>>> = ({ data, selected }) => {
  const isGroup = data.is_group;
  const isCollapsed = data.is_collapsed;

  if (isGroup) {
    return (
      <div
        className={`px-4 py-3 rounded-2xl border-2 transition-all duration-200 shadow-lg min-w-[220px] backdrop-blur-md ${
          selected
            ? 'bg-gradient-to-r from-purple-900/90 to-indigo-900/90 text-white border-purple-400 ring-4 ring-purple-500/30'
            : 'bg-indigo-950/80 text-white border-indigo-500/40 hover:border-indigo-400'
        }`}
      >
        <Handle type="target" position={Position.Left} className="!w-2.5 !h-2.5 !bg-indigo-400" />

        <div className="flex items-center justify-between gap-2 border-b border-indigo-500/30 pb-2 mb-2">
          <div className="flex items-center gap-1.5 text-xs font-bold text-indigo-200 truncate">
            {isCollapsed ? <Folder className="w-4 h-4 text-amber-400" /> : <FolderOpen className="w-4 h-4 text-amber-300" />}
            <span className="truncate">{data.label}</span>
          </div>

          {data.onToggleCollapse && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                data.onToggleCollapse?.(data.id);
              }}
              className="px-2 py-0.5 rounded-lg bg-white/10 hover:bg-white/20 text-[10px] font-bold text-white transition-colors flex items-center gap-1"
              title={isCollapsed ? 'Expand directory cluster' : 'Collapse directory cluster'}
            >
              <span>{isCollapsed ? `+${data.child_count || ''}` : 'Collapse'}</span>
              {isCollapsed ? <ChevronRight className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            </button>
          )}
        </div>

        <div className="text-[10px] text-indigo-300 font-mono flex items-center justify-between">
          <span>Directory Cluster</span>
          <span className="px-1.5 py-0.5 rounded bg-indigo-500/30 text-indigo-100 font-bold">
            {data.child_count || 0} modules
          </span>
        </div>

        <Handle type="source" position={Position.Right} className="!w-2.5 !h-2.5 !bg-purple-400" />
      </div>
    );
  }

  return (
    <div
      onClick={() => data.onSelectNode?.(data)}
      className={`px-4 py-3 rounded-2xl border cursor-pointer transition-all duration-200 shadow-md min-w-[210px] max-w-[260px] backdrop-blur-md ${
        selected
          ? 'bg-gradient-to-r from-indigo-600 to-purple-600 text-white border-indigo-300 ring-4 ring-indigo-500/30 shadow-indigo-500/40 scale-105'
          : 'bg-white/95 text-slate-800 border-indigo-200 hover:border-indigo-400 hover:shadow-lg dark:bg-slate-900/95 dark:text-slate-100 dark:border-indigo-900/70'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!w-2.5 !h-2.5 !bg-indigo-500" />

      <div className="flex items-center justify-between gap-2">
        <span
          className={`text-[9px] font-extrabold uppercase px-2 py-0.5 rounded tracking-wider ${
            selected
              ? 'bg-white/20 text-white'
              : 'bg-indigo-50 text-indigo-700 border border-indigo-100 dark:bg-indigo-950/80 dark:text-indigo-300 dark:border-indigo-800'
          }`}
        >
          {data.type || 'Component'}
        </span>
      </div>

      <h4 className="font-bold text-xs mt-1.5 leading-tight truncate">{data.label}</h4>

      {data.file && (
        <div
          className={`text-[10px] font-mono truncate mt-1 flex items-center gap-1 opacity-80 ${
            selected ? 'text-indigo-100' : 'text-slate-500 dark:text-slate-400'
          }`}
        >
          <Code className="w-3 h-3 shrink-0" />
          <span className="truncate">{data.file}</span>
        </div>
      )}

      {data.data_passed && data.data_passed.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {data.data_passed.slice(0, 3).map((dp, idx) => (
            <span
              key={idx}
              className={`text-[9px] font-mono px-1.5 py-0.5 rounded border ${
                selected
                  ? 'bg-emerald-500/30 text-emerald-200 border-emerald-400/40'
                  : 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800'
              }`}
            >
              {dp}
            </span>
          ))}
        </div>
      )}

      <Handle type="source" position={Position.Right} className="!w-2.5 !h-2.5 !bg-purple-500" />
    </div>
  );
};

const nodeTypes: NodeTypes = {
  architectureNode: ArchitectureNode as any,
};

// --- Dagre 2D Graph Layout Engine ---
const getLayoutedElements = (nodes: Node[], edges: Edge[], direction = 'LR') => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));

  const isHorizontal = direction === 'LR';
  dagreGraph.setGraph({ rankdir: direction, ranksep: 90, nodesep: 45 });

  nodes.forEach((node) => {
    const isGroup = node.data?.is_group;
    const width = isGroup ? 240 : 220;
    const height = isGroup ? 100 : 90;
    dagreGraph.setNode(node.id, { width, height });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const layoutedNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    const isGroup = node.data?.is_group;
    const width = isGroup ? 240 : 220;
    const height = isGroup ? 100 : 90;

    return {
      ...node,
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
      position: {
        x: (nodeWithPosition?.x || 0) - width / 2,
        y: (nodeWithPosition?.y || 0) - height / 2,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
};

// --- Inner Graph Canvas Component ---
const GraphCanvasInternal: React.FC<{
  rawNodesData: Record<string, any> | any[];
  rawEdgesData: any[];
  diagramType?: string;
  onRefresh?: () => void;
}> = ({ rawNodesData, rawEdgesData, diagramType, onRefresh }) => {
  const { fitView, zoomIn, zoomOut } = useReactFlow();

  const [direction, setDirection] = useState<'LR' | 'TB'>('LR');
  const [collapsedDirs, setCollapsedDirs] = useState<Record<string, boolean>>({});
  const [selectedNode, setSelectedNode] = useState<any | null>(null);

  const toggleDirectoryCollapse = useCallback((dirId: string) => {
    setCollapsedDirs((prev) => ({
      ...prev,
      [dirId]: !prev[dirId],
    }));
  }, []);

  const handleSelectNode = useCallback((nodeData: any) => {
    setSelectedNode(nodeData);
  }, []);

  // Compute graph nodes and edges from props and collapsed state
  const { initialNodes, initialEdges } = useMemo(() => {
    let nodeDict: Record<string, any> = {};
    if (Array.isArray(rawNodesData)) {
      rawNodesData.forEach((n) => {
        if (n && n.id) nodeDict[n.id] = n;
      });
    } else if (rawNodesData && typeof rawNodesData === 'object') {
      nodeDict = rawNodesData;
    }

    // If no nodes, return empty graph without injecting hardcoded RepoTalk files
    if (Object.keys(nodeDict).length === 0) {
      return { initialNodes: [], initialEdges: [] };
    }

    // Count children per directory cluster
    const dirChildCounts: Record<string, number> = {};
    Object.values(nodeDict).forEach((n: any) => {
      if (n.dir && !n.is_group) {
        const did = 'dir_' + n.dir.replace(/\//g, '_').replace(/\./g, '_');
        dirChildCounts[did] = (dirChildCounts[did] || 0) + 1;
      }
    });

    // Build directory container nodes if not present
    Object.keys(dirChildCounts).forEach((did) => {
      if (!nodeDict[did]) {
        const dirName = did.replace(/^dir_/, '').replace(/_/g, '/');
        nodeDict[did] = {
          id: did,
          label: dirName,
          type: 'Directory Cluster',
          file: dirName,
          dir: '',
          is_group: true,
        };
      }
      nodeDict[did].child_count = dirChildCounts[did];
    });

    const flowNodes: Node[] = [];
    const flowEdges: Edge[] = [];
    const edgeKeySet = new Set<string>();

    // Add nodes according to collapsed directory rules
    Object.values(nodeDict).forEach((n: any) => {
      const parentDirId = n.dir ? 'dir_' + n.dir.replace(/\//g, '_').replace(/\./g, '_') : '';
      const isParentCollapsed = parentDirId && collapsedDirs[parentDirId];

      if (!n.is_group && isParentCollapsed) {
        // Skip child node rendering when parent directory cluster is collapsed
        return;
      }

      flowNodes.push({
        id: n.id,
        type: 'architectureNode',
        data: {
          ...n,
          is_collapsed: !!collapsedDirs[n.id],
          onToggleCollapse: toggleDirectoryCollapse,
          onSelectNode: handleSelectNode,
        },
        position: { x: 0, y: 0 },
      });
    });

    // Helper to add edge safely
    const addFlowEdge = (src: string, tgt: string, label: string = '') => {
      // Re-route target or source if collapsed into directory cluster
      let actualSrc = src;
      let actualTgt = tgt;

      const srcNode = nodeDict[src];
      const tgtNode = nodeDict[tgt];

      if (srcNode && srcNode.dir) {
        const srcDirId = 'dir_' + srcNode.dir.replace(/\//g, '_').replace(/\./g, '_');
        if (collapsedDirs[srcDirId]) actualSrc = srcDirId;
      }
      if (tgtNode && tgtNode.dir) {
        const tgtDirId = 'dir_' + tgtNode.dir.replace(/\//g, '_').replace(/\./g, '_');
        if (collapsedDirs[tgtDirId]) actualTgt = tgtDirId;
      }

      if (actualSrc === actualTgt) return;
      const key = `${actualSrc}->${actualTgt}`;
      if (edgeKeySet.has(key)) return;
      edgeKeySet.add(key);

      flowEdges.push({
        id: `e_${actualSrc}_${actualTgt}`,
        source: actualSrc,
        target: actualTgt,
        label: label,
        animated: true,
        type: 'smoothstep',
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: '#6366f1',
          width: 18,
          height: 18,
        },
        style: {
          strokeWidth: 2,
          stroke: '#6366f1',
        },
      });
    };

    // Add edges explicitly provided in rawEdgesData
    if (Array.isArray(rawEdgesData)) {
      rawEdgesData.forEach((e) => {
        if (e.source && e.target) {
          addFlowEdge(e.source, e.target, e.label || '');
        }
      });
    }

    // Add connected_to links from node data
    Object.values(nodeDict).forEach((n: any) => {
      if (n.connected_to && Array.isArray(n.connected_to)) {
        n.connected_to.forEach((targetId: string) => {
          const label = n.data_passed && n.data_passed[0] ? n.data_passed[0] : '';
          addFlowEdge(n.id, targetId, label);
        });
      }
    });

    const layouted = getLayoutedElements(flowNodes, flowEdges, direction);
    return { initialNodes: layouted.nodes, initialEdges: layouted.edges };
  }, [rawNodesData, rawEdgesData, collapsedDirs, direction, toggleDirectoryCollapse, handleSelectNode]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
    setTimeout(() => {
      fitView({ padding: 0.25, duration: 400 });
    }, 50);
  }, [initialNodes, initialEdges, setNodes, setEdges, fitView]);

  return (
    <div className="relative w-full h-[620px] rounded-2xl overflow-hidden glass-panel border border-white/70 shadow-xl bg-gradient-to-br from-slate-50/80 via-indigo-50/30 to-purple-50/20 dark:from-slate-950/90 dark:via-indigo-950/40 dark:to-purple-950/30">
      {/* Top Floating Control Toolbar */}
      <div className="absolute top-4 right-4 z-20 flex items-center gap-1.5 p-1.5 rounded-2xl glass-panel border border-white/80 dark:border-white/10 shadow-lg">
        <button
          onClick={() => zoomIn({ duration: 300 })}
          className="p-2 rounded-xl text-slate-700 hover:bg-white/80 dark:text-slate-300 dark:hover:bg-slate-800 transition-colors"
          title="Zoom In"
        >
          <ZoomIn className="w-4 h-4" />
        </button>
        <button
          onClick={() => zoomOut({ duration: 300 })}
          className="p-2 rounded-xl text-slate-700 hover:bg-white/80 dark:text-slate-300 dark:hover:bg-slate-800 transition-colors"
          title="Zoom Out"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        <button
          onClick={() => fitView({ padding: 0.25, duration: 400 })}
          className="p-2 rounded-xl text-slate-700 hover:bg-white/80 dark:text-slate-300 dark:hover:bg-slate-800 transition-colors"
          title="Zoom to Fit Canvas"
        >
          <Maximize2 className="w-4 h-4" />
        </button>
        <button
          onClick={() => setDirection((d) => (d === 'LR' ? 'TB' : 'LR'))}
          className="px-2.5 py-1.5 rounded-xl text-xs font-bold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950 dark:text-indigo-300 transition-colors flex items-center gap-1"
          title="Toggle Canvas Orientation (Horizontal / Vertical)"
        >
          <Sliders className="w-3.5 h-3.5" />
          <span>{direction}</span>
        </button>

        {onRefresh && (
          <button
            onClick={onRefresh}
            className="p-2 rounded-xl text-indigo-600 hover:bg-indigo-50 dark:text-indigo-400 dark:hover:bg-indigo-950 transition-colors"
            title="Refresh Architecture Graph"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* React Flow 2D Canvas or Empty State */}
      {nodes.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-full text-center p-8">
          <Code className="w-12 h-12 text-slate-400 mb-3" />
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300">No Architecture Components Found</h3>
          <p className="text-xs text-slate-500 max-w-sm mt-1">
            No architecture nodes or components were identified for this repository.
          </p>
        </div>
      ) : (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          fitView
          minZoom={0.2}
          maxZoom={2.5}
          className="w-full h-full"
        >
          <Background gap={20} size={1} color="#818cf8" />
          <MiniMap
            nodeColor={(n: any) => {
              if (n.data?.is_group) return '#818cf8';
              if (n.data?.type?.includes('UI') || n.data?.type?.includes('Page')) return '#6366f1';
              if (n.data?.type?.includes('Service')) return '#a855f7';
              if (n.data?.type?.includes('Database')) return '#10b981';
              return '#94a3b8';
            }}
            maskColor="rgba(15, 23, 42, 0.4)"
            className="!bottom-4 !right-4 !w-36 !h-28"
          />
        </ReactFlow>
      )}

      {/* Slide-out Node Detail Side Drawer */}
      <AnimatePresence>
        {selectedNode && (
          <motion.div
            initial={{ opacity: 0, x: 320 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 320 }}
            transition={{ duration: 0.25 }}
            className="absolute top-4 right-4 bottom-4 w-96 glass-panel rounded-2xl p-6 border border-white/90 dark:border-white/10 shadow-2xl z-30 flex flex-col justify-between overflow-y-auto bg-white/95 dark:bg-slate-900/95"
          >
            <div className="space-y-4">
              <div className="flex items-start justify-between">
                <div className="space-y-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-600 bg-indigo-50 dark:bg-indigo-950 dark:text-indigo-300 px-2 py-0.5 rounded border border-indigo-100 dark:border-indigo-900">
                    {selectedNode.type || 'Component Module'}
                  </span>
                  <h3 className="text-lg font-bold text-slate-800 dark:text-slate-100 leading-tight">
                    {selectedNode.label}
                  </h3>
                </div>
                <button
                  onClick={() => setSelectedNode(null)}
                  className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {selectedNode.file && (
                <div className="text-xs font-mono text-slate-600 dark:text-slate-300 bg-slate-100/80 dark:bg-slate-800/80 p-2.5 rounded-xl border border-slate-200/70 dark:border-slate-700/70 flex items-center gap-2">
                  <Code className="w-4 h-4 text-indigo-500 shrink-0" />
                  <span className="truncate">{selectedNode.file}</span>
                </div>
              )}

              <div className="space-y-2 text-xs">
                <span className="font-bold text-slate-700 dark:text-slate-200 flex items-center gap-1.5">
                  <Sparkles className="w-4 h-4 text-amber-500" />
                  <span>Architecture Pass Breakdown:</span>
                </span>
                <p className="text-slate-600 dark:text-slate-300 leading-relaxed bg-white/80 dark:bg-slate-800/60 p-3.5 rounded-xl border border-slate-200/60 dark:border-slate-700/60 shadow-sm">
                  {selectedNode.description ||
                    `Module '${selectedNode.label}' executes core operations and links codebase dependencies in ${diagramType || 'analysis pass'}.`}
                </p>
              </div>

              {selectedNode.data_passed && selectedNode.data_passed.length > 0 && (
                <div className="space-y-1.5 text-xs">
                  <span className="font-semibold text-slate-500 dark:text-slate-400">Traced Data Items / Payload:</span>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedNode.data_passed.map((item: string, idx: number) => (
                      <span
                        key={idx}
                        className="px-2.5 py-1 rounded-lg bg-emerald-50 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-300 font-mono text-[11px] border border-emerald-200 dark:border-emerald-800"
                      >
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {selectedNode.connected_to && selectedNode.connected_to.length > 0 && (
                <div className="space-y-1.5 text-xs">
                  <span className="font-semibold text-slate-500 dark:text-slate-400">Connected Graph Targets:</span>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedNode.connected_to.map((conn: string, idx: number) => (
                      <span
                        key={idx}
                        className="px-2.5 py-1 rounded-lg bg-indigo-50 dark:bg-indigo-950/80 text-indigo-700 dark:text-indigo-300 font-mono text-[11px] border border-indigo-100 dark:border-indigo-900"
                      >
                        → {conn}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="pt-4 border-t border-slate-200 dark:border-slate-800">
              <button
                onClick={() => setSelectedNode(null)}
                className="w-full py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-semibold text-xs shadow-md shadow-indigo-500/20 hover:opacity-90 transition-all flex items-center justify-center gap-1.5"
              >
                <span>Close Detail Drawer</span>
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

// Outer wrapper providing ReactFlowProvider
export const InteractiveMindMap: React.FC<{
  data: {
    diagram_type?: string;
    nodes?: Record<string, any> | any[];
    edges?: any[];
    mind_map?: any;
  } | null;
  onRefresh?: () => void;
  className?: string;
}> = ({ data, onRefresh, className = '' }) => {
  // Normalize legacy data or standard nodes & edges
  const rawNodes = data?.nodes || [];
  const rawEdges = data?.edges || [];

  return (
    <div className={`w-full ${className}`}>
      <ReactFlowProvider>
        <GraphCanvasInternal
          rawNodesData={rawNodes}
          rawEdgesData={rawEdges}
          diagramType={data?.diagram_type}
          onRefresh={onRefresh}
        />
      </ReactFlowProvider>
    </div>
  );
};
