'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Navigation } from '@/components/shell/Navigation';
import { AppHeader } from '@/components/shell/AppHeader';
import { AnimatePresence, motion, MotionConfig } from 'framer-motion';

const NAV_COLLAPSED_KEY = 'repotalks_nav_collapsed';

export const AppShell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(NAV_COLLAPSED_KEY);
      if (stored === 'true') setNavCollapsed(true);
    } catch {}
  }, []);

  const toggleNav = useCallback(() => {
    setNavCollapsed((prev) => {
      const next = !prev;
      try { localStorage.setItem(NAV_COLLAPSED_KEY, String(next)); } catch {}
      return next;
    });
  }, []);

  const closeMobileMenu = useCallback(() => setMobileMenuOpen(false), []);

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex h-dvh overflow-hidden bg-canvas">
        {/* Desktop navigation */}
        <div className="hidden md:flex">
          <Navigation collapsed={navCollapsed} onToggle={toggleNav} />
        </div>

        {/* Mobile menu overlay */}
        <AnimatePresence>
          {mobileMenuOpen && (
            <>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="fixed inset-0 z-40 bg-foreground/20 md:hidden"
                onClick={closeMobileMenu}
                aria-hidden="true"
              />
              <motion.div
                initial={{ x: -280 }}
                animate={{ x: 0 }}
                exit={{ x: -280 }}
                transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
                className="fixed inset-y-0 left-0 z-50 w-[280px] md:hidden"
              >
                <Navigation collapsed={false} onToggle={closeMobileMenu} onNavigate={closeMobileMenu} />
              </motion.div>
            </>
          )}
        </AnimatePresence>

        {/* Main content area */}
        <div className="flex-1 flex flex-col min-w-0 min-h-0">
          <AppHeader onMenuToggle={() => setMobileMenuOpen(true)} />
          <main
            role="main"
            className="flex-1 overflow-y-auto overflow-x-hidden"
          >
            <div className="p-4 md:p-6 max-w-[1600px] mx-auto">
              {children}
            </div>
          </main>
        </div>
      </div>
    </MotionConfig>
  );
};
