'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { Search, Menu, Sun, Moon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import { useTheme } from '@/context/ThemeContext';
import { QuickSearchModal } from '@/components/QuickSearchModal';

interface AppHeaderProps {
  onMenuToggle: () => void;
}

export const AppHeader: React.FC<AppHeaderProps> = ({ onMenuToggle }) => {
  const router = useRouter();
  const pathname = usePathname();
  const { activeRepo } = useProject();
  const { resolvedTheme, toggleTheme, mounted } = useTheme();
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  const handleSearchResultSelect = useCallback((result: any) => {
    setIsSearchOpen(false);
    if (result.path) {
      if (pathname !== '/') {
        router.push('/');
      }
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent('repotalks:select-file', { detail: { path: result.path } }));
      }, 100);
    }
  }, [pathname, router]);

  // Global Ctrl+K / Cmd+K listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsSearchOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <>
      <header className="shrink-0 flex items-center justify-between px-4 md:px-6 py-3 border-b border-border bg-surface/80 backdrop-blur-sm z-10">
        <div className="flex items-center gap-3 min-w-0">
          {/* Mobile menu button */}
          <button
            onClick={onMenuToggle}
            className="p-2 -ml-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors md:hidden"
            aria-label="Open menu"
          >
            <Menu className="w-5 h-5" />
          </button>

          {/* Active repo anchor */}
          {activeRepo && (
            <div className="flex items-center gap-2 text-label text-foreground min-w-0">
              <span className="font-semibold truncate">{activeRepo.name}</span>
              <span className="text-meta text-muted-foreground hidden sm:inline">
                {activeRepo.file_count} files
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Search button */}
          <button
            onClick={() => setIsSearchOpen(true)}
            className={cn(
              'flex items-center gap-2 px-3 py-1.5 rounded-md border border-border',
              'text-meta text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors'
            )}
          >
            <Search className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Search…</span>
            <kbd className="px-1.5 py-0.5 rounded bg-muted-surface border border-border text-[10px] font-mono font-medium hidden sm:inline">
              ⌘K
            </kbd>
          </button>

          {/* Theme toggle */}
          <button
            onClick={toggleTheme}
            className="p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted-surface transition-colors"
            aria-label={mounted ? `Switch to ${resolvedTheme === 'dark' ? 'light' : 'dark'} theme` : 'Toggle theme'}
          >
            {mounted && resolvedTheme === 'dark' ? (
              <Sun className="w-4 h-4" />
            ) : mounted && resolvedTheme === 'light' ? (
              <Moon className="w-4 h-4" />
            ) : (
              <span className="w-4 h-4 inline-block" />
            )}
          </button>
        </div>
      </header>

      {/* Quick Search Modal */}
      <QuickSearchModal
        isOpen={isSearchOpen}
        onClose={() => setIsSearchOpen(false)}
        activeRepoId={activeRepo?.repo_id || null}
        onSelectResult={handleSearchResultSelect}
      />
    </>
  );
};
