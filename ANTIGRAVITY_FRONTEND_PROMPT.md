# RepoTalks — paste-ready Antigravity implementation prompt

Copy everything below the divider into Antigravity in this project. The prompt is self-contained; `FRONTEND_UX_REDESIGN_PLAN.md` supplies the full acceptance matrix and additional detail.

---

Act as a senior product designer, design engineer, frontend architect, accessibility specialist and QA engineer. Implement a complete frontend redesign of this existing RepoTalks application. Deliver working production-oriented code across every existing route, not a concept, static mockup, CSS reskin or landing page. Use the following design brief as the proposed direction and make routine implementation decisions autonomously. Inspect the current project before changing it, preserve unrelated work, and complete the implementation and verification instead of stopping after a plan.

Read `FRONTEND_UX_REDESIGN_PLAN.md` in the project root if present. It is the detailed task specification. Also read applicable AGENTS.md instructions, `frontend/package.json`, `frontend/next.config.js`, `frontend/app/layout.tsx`, `frontend/app/globals.css`, `frontend/tailwind.config.js`, all six page files, both contexts, `frontend/lib/api.ts`, `frontend/lib/storage.ts`, relevant components, and the actual endpoint contracts in `backend/main.py`. The audit evidence is in `.audit/design-assessment-b.md` and `output/playwright/`. Verify findings against the current source; files may have changed since the audit. Do not assume git or rollback is available: the audited workspace was not an accessible git checkout.

PRODUCT AND EXISTING STACK

RepoTalks helps developers and students understand a codebase, inspect supporting code, explain the project to different audiences and prepare for a viva/interview. It accepts public GitHub repositories and ZIP archives. Existing capabilities include repository switching/deletion, file-scoped streaming chat, image attachment, citations, quick search, architecture maps, AI-assisted code traces, audience explanations, question-bank practice, spoken/text viva answers, evaluation, session summaries, Markdown/print export and API-key/theme settings.

Current stack: Next.js 14 App Router, React 18, TypeScript, Tailwind CSS 3, Framer Motion 11, Lucide, React Markdown/GFM, React Flow and Dagre. Keep this stack unless a demonstrated blocker requires a separately explained change. This task does not authorize a framework migration or rewriting the backend.

Preserve the six URLs: `/`, `/architecture`, `/tracer`, `/audience`, `/viva`, `/settings`. Preserve `output: 'export'` and FastAPI serving the exported frontend. Preserve `NEXT_PUBLIC_API_URL`, `X-Session-Id`, `X-Gemini-API-Key`, JSON versus FormData requests, POST SSE streaming, abort signals, response shapes and storage keys or explicit migrations. Do not introduce Server Actions, Next server-only API routes, runtime cookie assumptions or a replacement auth/AI backend.

DESIGN DIRECTION

Internal direction: “Code Atlas.” Keep the RepoTalks product name. Build a precise, calm code-understanding workspace. The signature interaction is question → explanation → inspectable repository evidence. Use that relationship to make the product distinctive. Prioritize developer task density, readable prose, clear context and strong recovery. Use shadcn-quality primitives and thoughtful composition; default library styling alone is not the design.

Visual system:
- Light canvas #F6F7F9; surface #FFFFFF; muted surface #EEF1F5; text #17212F; muted text #5C6879; border #DCE2EA; input boundary #7B8797; primary #2457D6 with white text; selected surface #EAF0FF with text #244CB0.
- Dark canvas #0E1116; surface #151A22; muted surface #1C2430; text #E8EDF5; muted text #A2AFC1; border #303B4B; input boundary #748196; primary #8BAFFF with #101827 text; selected surface #203456 with #C6D7FF text.
- Success/warning/error are semantic and always accompanied by words or icons. Define their surface/foreground pairs and verify contrast. Use one semantic CSS-variable system mapped to Tailwind 3 and shadcn roles; no ad hoc palette per page.
- Prefer Geist Sans and Geist Mono, self-hosted with licensed assets and robust system fallbacks. Use 24/32px page titles, 18/26px section titles, 16/26px explanation text, 14/20px UI labels, 13–14px code and at least 12px supporting metadata. Mobile editable fields should be at least 16px.
- Spacing: 4/8/12/16/24/32/48px. Gutters: 24px desktop, 16px phone. Prose: 65–75ch. Radii: 8px controls, 12px panels, 16px dialogs. Quiet borders; shadows only where elevation matters.
- Default theme is System; Light and Dark must both work on every route, dialog, code block and graph surface. Initialize theme before paint safely without making the whole application wait for mount.

REJECT THESE PATTERNS

No pervasive glass/blur, purple gradient buttons, rainbow feature tiles, animated sparkling logos, glowing borders, giant empty hero sections, decorative statistics, fake repositories, invented readiness/security scores, constant pulsing, per-card stagger animations, typewriter replays of already streamed text, cursor effects or background particles. Do not replace the existing app with a marketing homepage. Do not remove capabilities to make screenshots cleaner. No inert buttons, empty onClick handlers, fabricated progress, false cancellation, made-up sources or unfinished TODO components in delivered flows.

COMPONENT STRATEGY

Use compatible shadcn primitives selectively: Button, Input, Textarea, Label, Dialog, AlertDialog, Sheet, Tabs, DropdownMenu, Tooltip, RadioGroup, Separator, Skeleton and Command when useful. Confirm generated code is compatible with React 18/Tailwind 3. Do not blindly run an initialization command that migrates Tailwind or React. Inspect dependency/source changes before accepting them. Reuse clsx and tailwind-merge for `cn()`.

21st.dev can inspire one named interaction at a time. Inspect component source/license/accessibility/dependency cost and adapt it to this system. Do not combine unrelated component aesthetics or add a second animation library. Use real semantic controls and accessible focus behavior; a component library does not automatically make a whole screen accessible.

Replace GlassCard with purpose-specific shell, pane and content structures. Organize components by responsibilities: ui primitives; shell; workspace; architecture; trace; practice; shared Markdown/code/evidence/state components. Separate route orchestration from domain state where that makes behavior easier to test. Avoid unnecessary abstractions or global-state dependencies.

REPAIR THESE REAL DEFECTS AS PART OF THE REDESIGN

1. FileExplorer stores a filter string but renders the original tree. Implement recursive name/path filtering, retain parent folders, reveal matches, show a no-results state and support Clear.
2. Global QuickSearchModal result selection only closes because no onSelectResult is supplied. Consolidate the duplicate instances into one global controller. Normalize file/symbol/chunk results and make mouse/Enter selection open the Workspace with correct repository/file/evidence context. Ctrl/Cmd+K works everywhere; Escape restores focus.
3. Workspace messages, selected file, draft and attachment survive repository switching. Scope them by repo ID or safely reset them. Never send repo A history/file context with a repo B request. Prefer separate in-memory sessions per repository.
4. Guard all chat, architecture, audience, trace and viva requests against stale completion. Abort on relevant context changes and compare request identity before success/error/finally updates. Key caches by repo plus parameters. Clear streaming timers and update assistant messages by ID, not “last array item.” A stale request must not clear a new request's busy flag.
5. ProjectContext catches backend errors and displays an empty project list. Separate boot/loading, valid empty, unavailable and ready; preserve last known context when appropriate with a clear unavailable indicator and Retry.
6. Replace browser alerts and swallowed errors with inline actionable recovery. Preserve URL/question/transcript and previous useful output on failure. Normalize response status errors consistently, including audience responses.
7. Replace pointer-only file/audience/trace divs with proper controls. Provide dialog semantics, focus trapping/restoration, selected states, field labels, named icon buttons, visible focus and live status announcements. Use either disclosure-list file navigation or a fully implemented keyboard tree, not incomplete ARIA.
8. Fix incomplete dark styles and any nested interactive elements, including the Settings Link/button.
9. Preserve viva transcripts until successful evaluation, block duplicate submissions and show unsupported/permission-denied/recognition-error states with text fallback.
10. Make citations actionable using real returned path/snippet metadata. Remove hardcoded model-name marketing and misleading runtime-trace claims.

SHELL AND RESPONSIVENESS

Navigation: Workspace, Architecture, Trace, Explain, Practice; Settings in the footer. Put the repository switcher and Add repository near the top. Put Delete in a repository menu with confirmation. Keep one clear context anchor; move repeated file counts, line counts, stack and infrastructure badges into repository details.

Wide desktop: roughly 224px nav, optional 240px file pane, primary conversation and optional 320–360px evidence inspector only if the conversation retains useful width. At narrower desktop widths the inspector is an overlay. Below 1200px, files default to a drawer/collapsed pane. Below 768px use a compact header, navigation Sheet and one working pane at a time. On mobile, provide explicit Files/Conversation controls and source evidence in a Sheet. Do not shrink text to fit columns.

Use 100dvh, correct min-height:0/min-width:0 and intentional scroll ownership. Composer stays reachable above the mobile keyboard. Long code scrolls within its panel. No document-wide horizontal overflow. The audit measured a 390px viewport expanding to 608px with a 300px sidebar: explicitly prevent this regression. Verify 320/390/768/1024/1440/1920px and zoom/reflow.

SCREEN REQUIREMENTS

Workspace first run:
One focused entry surface: “Understand your repository.” Labeled public GitHub URL, Add repository and alternate Upload ZIP. Show actual recent projects only when returned. Explain credential requirements when known; a missing browser-stored key is not proof the server has no key. Preserve entered URL through Settings. Disable competing input while submitting. Show honest indeterminate processing and elapsed time; success reveals real repository context and suggested starter questions.

Active Workspace:
Remove permanent ingestion banner. Make chat a readable document surface. Use multiline composer with Enter/Shift+Enter and IME support, file context chip, image preview/removal, Send/Stop, preserved drafts and useful retry. File selection must not overwrite an existing question; Explain this file is an explicit action. Auto-follow streaming only when near the bottom; otherwise offer Jump to latest. Citations open a shared evidence inspector. Reuse a themed Markdown/CodeBlock renderer with safe links, overflow handling and working copy feedback.

Architecture:
Keep React Flow/Dagre and its lazy import. Give the canvas space; compact Components/API flow/Data flow/Database view controls, Generate/Refresh, fit and existing layout tools. Readable nodes, restrained edges, selected connections and metadata inspector. Add a keyboard-accessible list alternative. Distinguish not-generated/loading/ready/no-data/error. Preserve a previous graph while refreshing. Refresh must bypass cache. Do not hide Database permanently because a filename heuristic missed it.

Trace:
Numbered steps and selected-step explanation/code. Keyboard selection, mobile previous/next and step picker, real source path, copy and useful context actions. Label it AI-assisted repository reasoning, not a verified runtime execution trace. Keep base narration; deeper narration is explicit. Aborted/failed partial narration must remain retryable and not be cached as a completed answer.

Explain:
Accessible audience selector for Developer, Recruiter, Manager, Professor; one Generate action. Present headline, pitch, highlights, detailed explanation and talking points as an editorial reading surface. Working Copy. Preserve old result during update and reject stale audience responses. Do not invent tone/length/share controls unsupported by contracts.

Practice:
Keep Study and Mock viva inside /viva. Study retains real progress, category/search filters, expandable answers, source references, practiced/shaky/unmarked status, Markdown export and print. Count statuses against the current bank. Mock viva shows one readable question, optional spoken playback, text answer, Record/Stop, Submit, evaluation and next/finish controls. Voice visuals stay subordinate and do not falsely imply real amplitude measurement. Stop speech/recognition on relevant navigation. Finish with an actual session summary and useful next step.

Settings:
Appearance and API access in a calm form column. Semantic Light/Dark/System radio group. Labeled key input, named reveal/hide, explicit Save/Remove and Test connection. Separate saved from tested status. Explain persistence truthfully (“Saved in this browser”). Do not log keys or expose them in screenshots/query strings. Remove unsupported model allocation claims.

BACKEND LIMITATIONS — DO NOT FAKE THESE

Current ingestion returns job_id after completion. Job/progress routes exist, but reliable correlation needs additional contract work; current cancellation status does not mean ingestion execution stopped. Baseline must use indeterminate processing. Do not build a fake percentage stepper or promise server cancellation from AbortController.

Existing citations/search/trace provide snippets and paths; a general source-file read endpoint was not found. Baseline source inspector displays available snippets, labelled honestly. Do not fabricate line numbers or a full-file editor. Put genuine progress/cancel and full-file reading into a separate backend-dependency note while completing the frontend-only baseline. Likewise, durable share links or cross-device chat history are new product scope.

MOTION

Use existing Framer Motion with MotionConfig reducedMotion='user', plus explicit CSS reduced-motion handling. Hover/focus 100–140ms; content changes 140–180ms; dialog/Sheet 180–220ms; citation/step selection 160–200ms. Favor opacity and small useful translations. Animate a message once, not every token. No continuous decoration, expensive full-screen effects or artificial waiting for animation. Every state must remain understandable without motion.

EXECUTION ORDER

1. Baseline the source, build/typecheck and rendered routes; identify pre-existing failures separately.
2. Repair request ownership, project state, search and filtering; add focused regression tests for real failure modes.
3. Implement tokens, fonts, primitives, both themes and responsive shell.
4. Complete Workspace end to end: add/select → ask → stop/retry → inspect source. This is the first visual review milestone, not the end of the task.
5. Migrate Architecture, Trace, Explain, Practice and Settings completely.
6. Add restrained motion, verify all states/viewports and fix the discovered issues in a bounded batch.
7. Deliver build/test results, screenshots, changed-files summary and implementation documentation reflecting actual code.

ACCEPTANCE AND PROOF

Run appropriate typecheck, production build, configured lint and behavioral checks. Do not report a command as passed if it did not run; resolve baseline setup/dependency problems explicitly. Verify direct navigation/refresh for all six exported routes through the intended serving setup.

Tests must cover file filtering, search selection from other routes, A/B repository switching during requests, stale finally handlers, stop/retry streaming, failed project fetch vs empty registry, missing/rejected key, true no-results vs request failure, voice fallback/failed evaluation and existing exports. Use clearly labelled deterministic API fixtures for UI states; separate fixture verification from real API integration.

Capture and inspect both themes and representative empty/loading/error/ready states at desktop/mobile widths. Check 200% zoom, keyboard-only use, focus trap/return, accessible field names, readable code, long content, mobile keyboard and reduced motion. Target WCAG 2.2 AA: verify actual contrast, focus and control targets. Prefer approximately 44px touch controls for comfort; respect the actual AA target-size criterion rather than misquoting it.

Preserve lazy graph loading, memoized message rows and batched streaming. Compare route JS/assets and client responsiveness with baseline; investigate unexplained >10% payload increases. LCP ≤2.5s, INP ≤200ms, CLS ≤0.1 are field goals, not invented audit scores. Measure AI wait separately. Only add virtualization when profiling representative data justifies it.

Before finishing, inspect screenshots yourself. The result should show a coherent product with readable answers and real evidence, restrained controls and purposeful screen compositions. Do not self-certify “award-winning” from a color change. Report exactly what was implemented, tested, visually inspected, and what remains limited by backend contracts. Complete all authorized frontend work; do not leave secondary routes broken or generic after polishing only the home page.
