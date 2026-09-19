'use client';

import React, { useState, useMemo, useCallback } from 'react';
import { ChevronRight, ChevronDown, File, Folder, Search, X } from 'lucide-react';
import { cn } from '@/lib/utils';

interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: TreeNode[];
  extension?: string;
}

interface FileExplorerProps {
  tree: TreeNode[];
  onSelectFile: (path: string) => void;
  selectedFilePath?: string;
}

function matchesFilter(node: TreeNode, query: string): boolean {
  const q = query.toLowerCase();
  if (node.name.toLowerCase().includes(q) || node.path.toLowerCase().includes(q)) return true;
  if (node.children) {
    return node.children.some((child) => matchesFilter(child, q));
  }
  return false;
}

function filterTree(nodes: TreeNode[], query: string): TreeNode[] {
  if (!query) return nodes;
  return nodes
    .map((node) => {
      if (node.type === 'directory' && node.children) {
        const filteredChildren = filterTree(node.children, query);
        if (filteredChildren.length > 0) {
          return { ...node, children: filteredChildren };
        }
        // Directory itself matches
        if (node.name.toLowerCase().includes(query.toLowerCase()) || node.path.toLowerCase().includes(query.toLowerCase())) {
          return node;
        }
        return null;
      }
      if (matchesFilter(node, query)) return node;
      return null;
    })
    .filter(Boolean) as TreeNode[];
}

function countFiles(nodes: TreeNode[]): number {
  let count = 0;
  for (const node of nodes) {
    if (node.type === 'file') count++;
    if (node.children) count += countFiles(node.children);
  }
  return count;
}

const TreeItem: React.FC<{
  node: TreeNode;
  depth: number;
  onSelect: (path: string) => void;
  selectedPath?: string;
  filterQuery: string;
}> = ({ node, depth, onSelect, selectedPath, filterQuery }) => {
  const [expanded, setExpanded] = useState(depth < 1 || !!filterQuery);
  const isSelected = selectedPath === node.path;
  const isDirectory = node.type === 'directory';

  // Auto-expand when filter is active
  React.useEffect(() => {
    if (filterQuery) setExpanded(true);
  }, [filterQuery]);

  const handleClick = () => {
    if (isDirectory) {
      setExpanded(!expanded);
    } else {
      onSelect(node.path);
    }
  };

  return (
    <div role="none">
      <button
        onClick={handleClick}
        role="treeitem"
        aria-expanded={isDirectory ? expanded : undefined}
        aria-selected={isSelected}
        aria-label={isDirectory ? `${node.name}, folder` : `${node.name}, file`}
        className={cn(
          'w-full flex items-center gap-1.5 px-2 py-1.5 text-left text-meta rounded-md transition-colors',
          isSelected
            ? 'bg-selected text-selected-foreground font-medium'
            : 'text-foreground hover:bg-muted-surface',
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        title={node.path}
      >
        {isDirectory ? (
          <>
            {expanded ? (
              <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
            )}
            <Folder className="w-3.5 h-3.5 text-primary shrink-0" />
          </>
        ) : (
          <>
            <span className="w-3.5" />
            <File className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          </>
        )}
        <span className="truncate">{node.name}</span>
      </button>

      {isDirectory && expanded && node.children && (
        <div role="group">
          {node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              onSelect={onSelect}
              selectedPath={selectedPath}
              filterQuery={filterQuery}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export const FileExplorer: React.FC<FileExplorerProps> = ({ tree, onSelectFile, selectedFilePath }) => {
  const [filterQuery, setFilterQuery] = useState('');

  const filteredTree = useMemo(() => filterTree(tree || [], filterQuery), [tree, filterQuery]);
  const matchCount = useMemo(() => countFiles(filteredTree), [filteredTree]);

  const handleClearFilter = useCallback(() => setFilterQuery(''), []);

  return (
    <div className="flex flex-col h-full">
      {/* Filter input */}
      <div className="px-3 py-2 border-b border-border shrink-0">
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-muted-foreground" />
          <input
            type="text"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="Filter files…"
            className="w-full pl-8 pr-8 py-1.5 rounded-md bg-muted-surface text-meta text-foreground border border-border placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 transition-colors"
            aria-label="Filter files by name or path"
          />
          {filterQuery && (
            <button
              onClick={handleClearFilter}
              className="absolute right-2 top-1.5 p-0.5 rounded text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Clear filter"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        {filterQuery && (
          <p className="text-[11px] text-muted-foreground mt-1.5 px-0.5">
            {matchCount === 0 ? 'No files match' : `${matchCount} file${matchCount !== 1 ? 's' : ''} found`}
          </p>
        )}
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1.5 px-1.5" role="tree" aria-label="Project files">
        {filteredTree.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <File className="w-8 h-8 text-muted-foreground/30 mb-2" />
            <p className="text-meta text-muted-foreground">
              {filterQuery ? 'No matching files found' : 'No files available'}
            </p>
            {filterQuery && (
              <button
                onClick={handleClearFilter}
                className="mt-2 text-meta text-primary hover:underline"
              >
                Clear filter
              </button>
            )}
          </div>
        ) : (
          filteredTree.map((node) => (
            <TreeItem
              key={node.path}
              node={node}
              depth={0}
              onSelect={onSelectFile}
              selectedPath={selectedFilePath}
              filterQuery={filterQuery}
            />
          ))
        )}
      </div>
    </div>
  );
};
