import React from 'react';
import type { Metadata } from 'next';
import './globals.css';
import { ThemeProvider } from '@/context/ThemeContext';
import { ProjectProvider } from '@/context/ProjectContext';
import { AppShell } from '@/components/shell/AppShell';

export const metadata: Metadata = {
  title: 'RepoTalks — Understand your repository',
  description: 'Ask about your codebase, trace execution flows, explore architecture, and prepare for technical interviews with AI-powered repository analysis.',
};

// Inline script to set theme class before first paint — prevents flash
const themeScript = `
(function() {
  try {
    var t = localStorage.getItem('repotalks_theme');
    var isDark = t === 'dark' || (!t || t === 'system') && window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (isDark) document.documentElement.classList.add('dark');
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="h-dvh overflow-hidden" style={{ fontFamily: "'Geist Sans', system-ui, -apple-system, sans-serif" }}>
        <ThemeProvider>
          <ProjectProvider>
            <AppShell>{children}</AppShell>
          </ProjectProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
