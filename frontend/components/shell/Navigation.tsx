'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  MessageSquare,
  Network,
  Compass,
  Users,
  BookOpen,
  Settings,
  ChevronLeft,
  ChevronRight,
  Trash2,
  FolderGit2,
  MoreHorizontal,
  Plus,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';

const NAV_ITEMS = [
  { href: '/', label: 'Workspace', icon: MessageSquare },
  { href: '/architecture', label: 'Architecture', icon: Network },
  { href: '/tracer', label: 'Trace', icon: Compass },
  { href: '/audience', label: 'Explain', icon: Users },
  { href: '/viva', label: 'Practice', icon: BookOpen },
];

interface NavigationProps {
  collapsed: boolean;
  onToggle: () => void;
  onNavigate?: () => void;
}

export const Navigation: React.FC<NavigationProps> = ({ collapsed, onToggle, onNavigate }) => {
  const pathname = usePathname();
  const { activeRepo, projects, selectProject, deleteProject, switchingProject } = useProject();
  const [deleteConfirmRepo, setDeleteConfirmRepo] = useState<any | null>(null);
  const [showRepoMenu, setShowRepoMenu] = useState(false);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const deleteModalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!deleteConfirmRepo) return;
    cancelBtnRef.current?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        setDeleteConfirmRepo(null);
        return;
      }
      if (e.key === 'Tab' && deleteModalRef.current) {
        const focusable = deleteModalRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [deleteConfirmRepo]);

  const handleDelete = async () => {
    if (!deleteConfirmRepo) return;
    await deleteProject(deleteConfirmRepo.repo_id);
    setDeleteConfirmRepo(null);
    setShowRepoMenu(false);
  };

  return (
    <aside
      className={cn(
        'h-full flex flex-col bg-surface border-r border-border transition-[width] duration-200 ease-out',
        collapsed ? 'w-[68px]' : 'w-[224px]'
      )}
    >
      {/* Logo + Collapse */}
      <div className="flex items-center justify-between px-3 py-4 border-b border-border shrink-0">
        <Link href="/" onClick={onNavigate} className="flex items-center gap-2.5 overflow-hidden min-w-0">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-primary-foreground shrink-0">
            <FolderGit2 className="w-4 h-4" />
          </div>
          {!collapsed && (
            <span className="text-label font-semibold text-foreground truncate">RepoTalks</span>
          )}
        </Link>
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors hidden md:flex"
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
        </button>
      </div>

      {/* Repository Switcher */}
      {!collapsed && (
        <div className="px-3 py-3 border-b border-border shrink-0">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-meta font-medium text-muted-foreground uppercase tracking-wider">Repository</span>
            {activeRepo && (
              <div className="relative">
                <button
                  onClick={() => setShowRepoMenu(!showRepoMenu)}
                  className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors"
                  aria-label="Repository options"
                >
                  <MoreHorizontal className="w-3.5 h-3.5" />
                </button>
                {showRepoMenu && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setShowRepoMenu(false)} />
                    <div className="absolute right-0 top-7 z-20 w-40 bg-surface border border-border rounded-lg shadow-lg py-1 animate-fade-in">
                      <button
                        onClick={() => {
                          setDeleteConfirmRepo(activeRepo);
                          setShowRepoMenu(false);
                        }}
                        className="w-full flex items-center gap-2 px-3 py-2 text-meta text-danger hover:bg-muted-surface transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        <span>Delete repository</span>
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
          {projects.length > 0 ? (
            <select
              disabled={switchingProject}
              value={activeRepo?.repo_id || ''}
              onChange={(e) => selectProject(e.target.value)}
              className="w-full px-2.5 py-1.5 rounded-md bg-muted-surface text-meta font-medium text-foreground border border-border focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 disabled:opacity-50 transition-colors"
            >
              {projects.map((p) => (
                <option key={p.repo_id} value={p.repo_id}>
                  {p.name}
                </option>
              ))}
            </select>
          ) : (
            <Link
              href="/"
              onClick={onNavigate}
              className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-muted-surface text-meta text-muted-foreground hover:text-foreground border border-dashed border-border hover:border-primary transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add repository</span>
            </Link>
          )}
        </div>
      )}

      {/* Navigation Items */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5" aria-label="Main navigation">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href;
          const Icon = item.icon;

          return (
            <Link key={item.href} href={item.href} onClick={onNavigate}>
              <div
                className={cn(
                  'flex items-center gap-2.5 px-3 py-2 rounded-md text-label transition-colors',
                  isActive
                    ? 'bg-selected text-selected-foreground font-semibold'
                    : 'text-muted-foreground hover:bg-muted-surface hover:text-foreground',
                  collapsed && 'justify-center px-2'
                )}
                aria-current={isActive ? 'page' : undefined}
                title={item.label}
              >
                <Icon className={cn('w-[18px] h-[18px] shrink-0', isActive ? 'text-selected-foreground' : '')} />
                {!collapsed && <span className="truncate">{item.label}</span>}
              </div>
            </Link>
          );
        })}
      </nav>

      {/* Footer: Settings */}
      <div className="px-2 py-2 border-t border-border shrink-0">
        <Link href="/settings" onClick={onNavigate}>
          <div
            className={cn(
              'flex items-center gap-2.5 px-3 py-2 rounded-md text-label transition-colors',
              pathname === '/settings'
                ? 'bg-selected text-selected-foreground font-semibold'
                : 'text-muted-foreground hover:bg-muted-surface hover:text-foreground',
              collapsed && 'justify-center px-2'
            )}
            aria-current={pathname === '/settings' ? 'page' : undefined}
            title="Settings"
          >
            <Settings className={cn('w-[18px] h-[18px] shrink-0', pathname === '/settings' ? 'text-selected-foreground' : '')} />
            {!collapsed && <span className="truncate">Settings</span>}
          </div>
        </Link>
      </div>

      {/* Delete Confirmation Dialog */}
      {deleteConfirmRepo && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-foreground/30" role="dialog" aria-modal="true" aria-label="Delete repository confirmation">
          <div ref={deleteModalRef} className="bg-surface border border-border rounded-xl shadow-xl max-w-sm w-full p-6 space-y-4 animate-slide-up">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-danger/10 flex items-center justify-center shrink-0">
                <Trash2 className="w-5 h-5 text-danger" />
              </div>
              <div>
                <h3 className="font-semibold text-label text-foreground">Delete repository?</h3>
                <p className="text-meta text-muted-foreground">This cannot be undone.</p>
              </div>
            </div>

            <div className="p-3 rounded-md bg-muted-surface text-meta font-mono text-foreground">
              {deleteConfirmRepo.name}
            </div>

            <div className="flex items-center justify-end gap-2">
              <button
                ref={cancelBtnRef}
                onClick={() => setDeleteConfirmRepo(null)}
                className="px-4 py-2 rounded-md bg-muted-surface text-label font-medium text-foreground hover:bg-border transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                className="px-4 py-2 rounded-md bg-danger text-white font-medium text-label hover:opacity-90 transition-opacity flex items-center gap-1.5"
              >
                <Trash2 className="w-3.5 h-3.5" />
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
};
