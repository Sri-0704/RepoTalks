# RepoTalks frontend audit and redesign implementation plan

Date: 17 September 2026. Deliverable: design and implementation specification, not an application rewrite.

Method: independent design assessment (`/root/design_review`) and technical evidence assessment (`/root/technical_evidence`), synthesized with source inspection and a bounded local browser audit. Browser evidence is recorded separately below. The Impeccable detector could not run because its engine was unavailable; no automated design score, Lighthouse result, or accessibility certification is claimed.

## 1. Executive decision

Rebuild the frontend as a **code understanding and practice workspace**. Its distinctive interaction is the connection between a question, an explanation, and the repository evidence supporting it. That relationship should determine the layout, visual hierarchy, and motion.

The present implementation uses a broadly consistent decorative vocabulary, but it gives branding, infrastructure labels, warnings, and useful content almost equal visual weight. Installing a component library alone will not solve that. The redesign must fix task structure, source navigation, request ownership, error recovery, accessibility, and responsive behavior as well as the appearance.

Recommended direction: **RepoTalks / Code Atlas**. “Code Atlas” is an internal design direction, not a product rename. Precise work surfaces, readable explanations, compact file paths, quiet navigation, and blue used for selection and action. The first memorable moment is selecting a source citation and seeing the relevant evidence, without losing the explanation.

Working assumptions: primary users are developers exploring a repository and students preparing to explain or defend their project. Desktop supports dense investigation; mobile supports complete task flows through one pane at a time. The product remains an application, not a marketing landing page. All existing routes and capabilities stay available.

## 2. Current product and implementation

| Area | Existing truth to preserve |
|---|---|
| Framework | Next.js 14 App Router, React 18, TypeScript, Tailwind CSS 3 |
| Components | Lucide icons, Framer Motion 11, React Markdown and GFM |
| Architecture | React Flow with Dagre, already dynamically loaded |
| Delivery | `output: 'export'`; FastAPI serves `frontend/out`; browser calls FastAPI directly |
| Repository input | Public GitHub URL or ZIP; active repository and project registry |
| Workspace | File tree, file-scoped chat, streaming stop, image attachment, citations |
| Learning | Four audience explanations; architecture views; inferred execution trace and narration |
| Practice | Question bank, practiced/shaky status, live viva, voice/text answer, evaluation, summary, Markdown and print export |
| Settings | User API key and light/dark/system appearance |

Keep the existing URLs: `/`, `/architecture`, `/tracer`, `/audience`, `/viva`, `/settings`. Rename navigation labels, not routes. README claims are not proof of currently implemented UI: for example, a health scorecard is described there but is not a current route. Do not invent a health dashboard to fill space.

## 3. Audit: prioritized findings

P1 means a task, context, or accessibility problem to resolve before release. P2 means an important quality/consistency repair. These are expert review judgments; functional findings are source-confirmed unless identified as a browser observation.

| ID | Priority | Finding / evidence | Required outcome |
|---|---|---|---|
| UX-01 | P1 | File filter stores text but maps the original tree. `frontend/components/FileExplorer.tsx:85,104,113`. | Recursive matching with ancestors retained, matches expanded, result count, clear filter and no-results state. |
| UX-02 | P1 | Search result selection only invokes an optional callback and closes; neither mounted modal supplies that callback. `QuickSearchModal.tsx:128`; `Header.tsx:97`; `app/page.tsx:403`. | One search controller. Pointer and Enter selection open the correct repository/file context and available evidence. |
| UX-03 | P1 | Chat aborts on repository change but retains messages, selected file, draft and attachment; old history is sent with a new repo ID. `app/page.tsx:81-92,196-213`. | Repository-scoped sessions, cancellation and request-identity checks. Never combine A's history/context with B's request. |
| UX-04 | P1 | Architecture, audience and tracer reset displayed results on repository change but do not consistently prevent late requests from writing into the new state. `architecture/page.tsx:49-88`; `audience/page.tsx:59-74`; `tracer/page.tsx:31-67`. | Abort old requests and reject stale success/error/finally updates. Cache by repository plus operation parameters. |
| UX-05 | P1 | A failed project fetch clears projects and active context and resembles a valid empty account. `context/ProjectContext.tsx:55-58`. | Separate initial loading, empty, ready and service unavailable. Preserve last known valid data with an explicit stale/unavailable indication. |
| UX-06 | P1 | Sidebar is always 300px or 80px; header contains nonwrapping metadata groups. `Sidebar.tsx:46-49`; `layout.tsx:26-32`; `Header.tsx:40-65`. | Responsive navigation drawer, compact header and intentional single-pane mobile workflows. Exact overflow is a browser check. |
| UX-07 | P1 | File rows, audience selection and trace hops are click-only divs; custom overlays lack standard dialog semantics/focus handling. `FileExplorer.tsx:32,49`; `audience/page.tsx:127`; `tracer/page.tsx:191`; `QuickSearchModal.tsx:137`; `Sidebar.tsx:207`. | Semantic controls, accessible dialogs, visible focus, predictable keyboard operation and restored focus. |
| UX-08 | P2 | Alerts replace recovery UI, search swallows failures, and audience API helper does not check response status. `lib/api.ts:377-388`; `QuickSearchModal.tsx:89`; route error handlers. | Typed errors with inline Retry; preserve input and prior useful output. Distinguish no results from failed search. |
| UX-09 | P2 | Every page inherits glass, blur, gradients, rounded containers and decorative entry motion. `globals.css:63-112,144-166`; `GlassCard.tsx:16-20`. | Opaque work surfaces, semantic tokens, fewer boxes, hierarchy through spacing and typography. |
| UX-10 | P2 | Chat and controls often use 12px, citations 10px, some graph tags 9px. `app/page.tsx:49,61`; `InteractiveMindMap.tsx`. | Readable 15–16px explanations, 13–14px code, compact but readable metadata. Verify contrast rather than assume failure from color names. |
| UX-11 | P2 | Audience and search contain fixed light colors; theme is applied after mount. `audience/page.tsx:154-205`; `QuickSearchModal.tsx:143-156`; `ThemeContext.tsx:23-57`. | Complete semantic dark theme and pre-paint initialization compatible with static export. |
| UX-12 | P2 | Repeated pulses and entry movement have no reduced-motion policy. `globals.css:201-221`; `GlassCard.tsx`; `VoiceRecorder.tsx:160-180`. | Motion follows task transitions; reduced-motion users receive equivalent static feedback. |
| UX-13 | P2 | Citations are comma-separated plain paths despite returned snippets. `app/page.tsx:60-63`; `backend/main.py:474-479`. | Evidence buttons reveal the available source snippet and path; never fabricate line numbers. |
| UX-14 | P2 | Voice errors are mostly console/alert feedback; submit clears the text before evaluation succeeds. `VoiceRecorder.tsx:46-49,81-84,105-107,198-204`. | Visible support/permission states, persistent text fallback, draft preservation and duplicate-submit protection. |
| UX-15 | P2 | Navigation and persistent chrome emphasize implementation labels; model names disagree between surfaces. `Header.tsx:81`; `Sidebar.tsx:199`; `layout.tsx:11`; settings and generation copy. | Task language in the main UI; model details only when backed by actual configuration and useful to the user. |

Additional fixes: replace nested Link/button in Header with one interactive element; label fields explicitly; disable competing ingestion inputs; make no-repository pages actionable; revoke export object URLs; check the unused MermaidViewer import against declared dependencies before trusting a clean build. Do not treat terminal encoding artifacts as verified user-facing copy defects.

### Heuristic assessment

Source-based expert scores on a 0–4 scale; not measured usability outcomes. Browser observations can confirm specific problems, not validate the score statistically.

| Heuristic | Score | Main issue |
|---|---:|---|
| Visibility of status | 2 | Loading exists, but failures can look empty |
| Real-world language | 2 | Implementation terminology dominates task language |
| User control | 2 | Stop exists; drafts, focus and recovery are inconsistent |
| Consistency | 2 | Common shell, incomplete theme/state conventions |
| Error prevention | 2 | Some disabled controls, context/concurrency defects |
| Recognition | 3 | Labeled navigation, tree and example questions |
| Efficiency | 2 | Useful shortcut undermined by inert search actions |
| Aesthetic/minimalist design | 1 | Decorative emphasis overwhelms work |
| Error recovery | 1 | Alerts, swallowed failures, premature draft clearing |
| Help/documentation | 1 | Little contextual troubleshooting |
| Total | **18/40** | Substantial redesign with functional repair |

Preserve the strengths: real file context, existing stream cancellation, deliberate generate actions, per-repository practice status, voice/text alternatives, lazy graph loading and memoized/batched chat rendering.

First-time users face setup warnings in multiple locations before their first useful result. Keyboard users encounter nonfocusable primary interactions. Experienced users lose trust when a filter does nothing or a repository change leaves unrelated answers onscreen. The intended emotional sequence is: clear start → honest waiting → useful explanation → inspectable evidence → a concrete next action.

## 4. Design contract

- **Screen job:** help a person understand, inspect and explain their own repository, then practice answering questions about it.
- **Hierarchy:** repository context → current task → working content/evidence → secondary tools/status.
- **Primary workflow:** add/select repository → ask a question → inspect evidence → explore a map/trace or practice an explanation.
- **Allowed building blocks:** quiet navigation, restrained tabs, document typography, source rows, code panels, accessible forms/dialogs, honest state feedback.
- **Required states:** initial loading, no repository, processing, ready, empty results, partial output, error, canceled request, unavailable source, unsupported microphone, permission denied, project switching and stale data.
- **Responsive rule:** reduce simultaneous panes before shrinking text. All core tasks remain possible on a phone.
- **Reject:** full-app glassmorphism, gradient primary buttons, multicolor icon tiles, decorative dashboard statistics, constant Sparkles/pulse animation, giant marketing heroes, floating chat ornaments, fake activity/progress, and controls that look complete but do nothing.

## 5. Visual design scheme

### Color and surfaces

Use the following as proposed starting tokens. The implementer must verify real foreground/background contrast, including hover/focus/disabled states, before release.

| Semantic role | Light | Dark | Usage |
|---|---|---|---|
| Canvas | `#F6F7F9` | `#0E1116` | App background |
| Surface | `#FFFFFF` | `#151A22` | Main work pane |
| Muted surface | `#EEF1F5` | `#1C2430` | Toolbar, code header, secondary selection |
| Foreground | `#17212F` | `#E8EDF5` | Primary text |
| Muted foreground | `#5C6879` | `#A2AFC1` | Supporting copy |
| Border | `#DCE2EA` | `#303B4B` | Subtle separation, not sole control boundary |
| Input/control boundary | `#7B8797` | `#748196` | Where a visible outline identifies a control |
| Primary | `#2457D6` | `#8BAFFF` | Primary action/selection |
| Primary foreground | `#FFFFFF` | `#101827` | Text on primary |
| Selected surface | `#EAF0FF` | `#203456` | Selected file/nav row |
| Selected foreground | `#244CB0` | `#C6D7FF` | Text on selected surface |
| Success foreground | `#176744` | `#82DDB0` | Confirmed success, paired with label/icon |
| Warning foreground | `#87510B` | `#F0C16F` | Actionable warning, paired with label/icon |
| Danger foreground | `#B12B3C` | `#FF9BA8` | Error/destructive action |
| Focus ring | `#2457D6` | `#A9C2FF` | 2px ring with visible offset |

Map tokens to shadcn roles: background, foreground, card, popover, muted, accent, primary, secondary, destructive, border, input and ring. In Tailwind 3, use a consistent RGB-channel or HSL-channel convention; do not paste Tailwind 4 `@theme` syntax into this project. Give destructive filled buttons their own verified foreground pair.

Default theme remains System. Both light and dark are first-class. Dark mode is not light CSS with a few `dark:` patches. Status color is reserved for status; graph categories use a small labelled palette and a legend.

### Typography, spacing and shape

Use **Geist Sans** for interface and prose, **Geist Mono** for source, paths and symbols; self-host licensed WOFF2 files through a compatible local-font setup, with `system-ui` and `ui-monospace` fallbacks. A dependency or network font failure must not break the build. Do not add a third display font.

| Role | Size / line height | Weight |
|---|---|---|
| First-run headline | 32 / 40px desktop; 28 / 36px phone | 600 |
| Page title | 24 / 32px | 600 |
| Section title | 18 / 26px | 600 |
| Answer/document text | 16 / 26px | 400 |
| UI labels and navigation | 14 / 20px | 500 |
| Code | 13 / 21px; 14px when space allows | 400 |
| Supporting metadata | 12 / 18px minimum | 400–500 |
| Mobile editable fields | 16px minimum | 400 |

Spacing scale: 4, 8, 12, 16, 24, 32, 48px. Main gutters 24px desktop, 20px tablet, 16px phone. Prose width 65–75ch. Controls 36–40px desktop and approximately 44px on touch surfaces; dense desktop rows may be smaller if target spacing and keyboard access remain sufficient. Radius 6px code, 8px controls, 12px panels, 16px dialogs; pills only for small status/context chips. Reserve shadows for floating surfaces, not every content section. Keep a single 1.5–1.75px icon stroke family and 16/18/20px icon sizes.

Product identity: a small repository/branch mark can replace the generic sparkling tile. Use code paths, source reference numbers and the visible explanation-to-evidence relationship as the main brand expression. No stock photography or decorative 3D assets are needed for these working screens.

### Reference use

- [shadcn/ui](https://ui.shadcn.com/docs): adopt owned, composable primitives; customize them to this contract. Its [Tailwind compatibility guidance](https://ui.shadcn.com/docs/tailwind-v4) explicitly retains a path for existing React 18/Tailwind 3 applications. Inspect generated changes before accepting them.
- [21st.dev](https://21st.dev): consult specific navigation, command or chat interaction examples, rather than importing a complete aesthetic from unrelated authors. Check each selected component's source, license, accessibility and dependency cost. No particular community component is preapproved here.
- [Linear](https://linear.app/features): reference for disciplined task density and restrained persistent chrome, not a pixel-copy specification.
- [UIZZE](https://uizze.com): optional reference catalogue for comparing workspace and source-inspector compositions. No paid reference retrieval was used in this audit.

## 6. Information architecture and layout

Navigation labels: **Workspace**, **Architecture**, **Trace**, **Explain**, **Practice**; Settings in the footer. Put the repository switcher at the top, next to the identity of the work. Keep Add repository within that switcher and as the primary first-run action. Project deletion belongs in a repository overflow menu and confirmation dialog, away from normal selection.

Persistent header: page title/breadcrumb, one compact active-repository anchor when needed, global search, theme/menu control. Move file counts, line counts and tech stack into repository details. Remove model badges and repeated active-context status blocks from persistent chrome.

Desktop workspace, sufficiently wide viewport:

```text
┌─────────────┬─────────────────────────────────────────────────────────┐
│ RepoTalks   │ Workspace / repository                     Search  Theme │
│ Repo switch├───────────────┬──────────────────────┬────────────────────┤
│             │ Files         │ Conversation         │ Source evidence    │
│ Workspace   │ Filter        │ Question             │ path / reference   │
│ Architecture│ folder        │ Readable explanation │ available snippet  │
│ Trace       │   file        │ [source 1] [source 2]│ linked context     │
│ Explain     │               │                      │                    │
│ Practice    │               ├──────────────────────┤                    │
│             │               │ context + composer   │                    │
│ Settings    │               │ Attach  Ask / Stop   │                    │
└─────────────┴───────────────┴──────────────────────┴────────────────────┘
```

This is an interaction schematic, not a fixed four-column requirement. Start with 224px navigation and 240px file pane, allow resizing/collapse within safe minima. Keep the answer column useful (roughly 560px or more) before docking a 320–360px inspector. Below the width needed for all panes, source evidence opens as an overlay; below 1200px, files default to an overlay/collapsed pane. Use available container width, not viewport breakpoints alone, to decide whether an inspector can dock.

| Width | Composition |
|---|---|
| ≥1440px | Expanded nav, optional files pane, conversation; dock evidence only when content minima fit |
| 1200–1439px | Nav plus files/conversation; evidence overlay |
| 768–1199px | Compact nav/rail, primary work pane, files and evidence overlays |
| <768px | 56px compact header; navigation Sheet; Files/Conversation as explicit view controls; evidence Sheet |

Use `100dvh` for app-workspace height, `min-height: 0` on flex/grid scroll children, explicit scroll ownership and safe-area padding. Reading/settings pages may scroll as documents. Mobile composer remains reachable above the keyboard; it must not cover the latest answer. Code can scroll horizontally inside its panel; the document must not scroll sideways. Graph panning is intentional and distinct from page overflow.

## 7. Screen-by-screen specification

### Workspace: first use and repository setup

Primary heading: **Understand your repository.** Supporting line: “Ask about the code, follow a flow, and prepare to explain it.” One central setup surface, approximately 640px wide. Labeled Public GitHub URL input and Add repository button; Upload ZIP as the alternate. Recent repositories appear only if actual repository data exists. Do not seed fake projects or unverifiable statistics.

Keep credential guidance conditional. A locally missing key is not proof that a server-configured key is absent. Use existing error/capability information, and provide a Settings link where a key is required. Preserve the repository URL when opening credentials.

For ingestion, disable competing submissions. Validate URL shape and ZIP type locally, and display server validation errors beside the input. Limits are configurable: backend defaults include 50MB ZIP and 10MB image, but UI must use a verified shared/configured value or avoid claiming a universal limit. Treat the server as authoritative.

Baseline processing UI: indeterminate progress, “Preparing your repository…”, elapsed time, and a plain explanation that larger repositories may take longer. Show only stages actually received. Do not claim a background task was canceled when the client only stopped waiting. Success transitions to the workspace with real name/counts and a suggested first question.

### Workspace: chat, files and evidence

Remove the permanent ingestion banner after activation. Chat gets the strongest content area. Assistant output reads like a document, with modest author/status labels instead of layers of chat bubbles; user messages are compact neutral/selected-surface blocks. Provide readable tables, lists, headings, code and long URLs through one Markdown renderer.

Composer: multiline Textarea, Enter sends except during IME composition, Shift+Enter inserts a newline, visible send/stop action, file context chip, image preview/removal and a useful disabled reason. Preserve unsent content on failure. Keep Stop available while streaming. Follow incoming output only while the user is near the bottom; otherwise show Jump to latest. Announce status changes, not every token, to assistive technology.

File selection sets context without overwriting an existing typed question. Offer Explain this file as a separate action. Filter names and paths recursively, retain ancestors and reveal matches. The baseline can use nested lists of disclosure buttons; use ARIA tree semantics only if implementing the full tree keyboard model.

Evidence inspector: clicking a citation highlights that citation and opens its path, available snippet and reference metadata. Return focus correctly when closed. A snippet is labelled as a snippet; missing source is a real state. Similarity is not confidence and should not be presented as an accuracy percentage. Reuse this inspector from search, chat and trace wherever the API supplies evidence.

### Architecture

Give the graph most of the viewport. Use a compact toolbar: view selector (Components / API flow / Data flow / Database), Generate or Refresh action, Fit view and layout controls already supported by the canvas. Maintain the existing React Flow/Dagre implementation and dynamic import.

Nodes show a readable label, type and optional path. Use restrained outlines/surfaces instead of glowing gradients. Selecting a node emphasizes its immediate connections and opens an inspector with actual returned metadata. Collapse group nodes without resetting the user's orientation unnecessarily. Provide a keyboard-accessible list alternative to the canvas.

Distinguish not generated, generating, ready, no relevant data and failed. Keep a previous result visible during regeneration with a clear updating indicator. Database availability inferred from filenames is a heuristic; do not permanently hide a view solely because that heuristic found no match. A true Refresh bypasses the current cache rather than returning it immediately.

### Trace

Title: **Trace a flow.** Helper: “Explore an AI-assisted path through the repository.” Do not market it as a verified runtime debugger or guaranteed call graph.

Desktop: numbered step list on the left and selected step's explanation/code on the right. On mobile use one selected step, previous/next controls and a steps drawer. Each step is a keyboard-operable button with selected state. Use a thin connector line as the only visual motif. Provide Copy snippet and Ask about this step only when they have real implementations.

Keep initial narration visible; deeper narration is explicit and cancellable. Cache by repository, trace identity and hop identity. A failed/aborted expansion must not permanently mark partial text as complete; expose Retry/Continue only when supported. No fake execution pulses or invented elapsed runtime values.

### Explain

Title: **Explain this project.** Use a labeled audience radio group or accessible select, with four succinct choices: Developer, Recruiter, Manager, Professor. One Generate explanation action. Avoid four rainbow cards competing with the explanation.

Output is a clean reading surface: headline, short pitch, key points, detailed explanation, talking points. Add Copy for real output. Preserve previous content while updating, label the intended audience, and ignore late responses for previous selections. Keep generated content distinct from interface copy. Do not invent controls for tone, length, language or share links unless existing contracts support them or separate scope is approved.

### Practice

Keep `/viva`, with two tabs: **Study** and **Mock viva**.

Study: compact progress summary based on actual question statuses, category/search controls, scannable question rows, expandable model answer and source references. Retain practiced/shaky/unmarked statuses and current Markdown/print exports. Count statuses against the current question bank, not all historical saved IDs. Preserve the per-repository storage convention.

Mock viva: one question at a time; topic/difficulty setup; large readable question; optional speech playback; visible answer Textarea; Record/Stop and Submit answer actions; evaluation followed by Next question or Finish session. The microphone visualizer is small and subordinate to the question/transcript. If it is not based on actual input amplitude, label it simply as a recording indicator.

States: unsupported speech recognition, permission denied, listening, transcript ready, evaluating, evaluation failed, evaluated, summary generating and complete. Stop speech/recognition when appropriate on route/repository change. Preserve transcript until evaluation succeeds. Prevent duplicate evaluation. End with an evidence-based session summary and a useful next practice action; no invented mastery percentage.

### Settings

Use a calm 720px reading/form column with Appearance and API access sections. Appearance is a semantic radio group with Light, Dark, System. API field has a programmatic label, accessible reveal/hide control and explicit Save/Remove actions, plus Test connection. Keep draft vs persisted key clear. State what persistence actually does in plain language (“Saved in this browser”), without claiming encryption or server behavior that has not been checked.

Connection states: not tested, testing, successful, failed. A saved key does not mean a tested working key. Do not expose key values in screenshots, logs or query parameters. Remove hardcoded model allocation tiles unless the UI can obtain trustworthy configuration and the information helps the task.

## 8. Motion specification

Use existing Framer Motion; do not add a second animation library. CSS handles simple hover/focus transitions.

| Interaction | Timing | Treatment |
|---|---|---|
| Hover/focus/pressed | 100–140ms | Background/border/opacity, no moving text |
| Tab/content change | 140–180ms | Short crossfade; keep layout stable |
| Dialog or Sheet | 180–220ms | Opacity plus small translation; predictable focus |
| Source selection | 160–200ms | Selected-source emphasis and inspector reveal |
| Newly appended message | 120ms | Optional opacity once, never per streamed token |
| Active trace step | 160ms | Selection/connector emphasis, user initiated |
| Success feedback | 140–180ms | One check/status change, no confetti |

Use an ease-out curve such as `cubic-bezier(0.2, 0.8, 0.2, 1)`. Avoid spring bounce for work panes and control labels. No per-card stagger, continuous shimmer, glowing borders, parallax, cursor effects or full-page entrance choreography.

Set `MotionConfig reducedMotion="user"`; handle CSS animations and special canvas effects separately with `prefers-reduced-motion`. The [Motion accessibility guide](https://motion.dev/docs/react-accessibility) documents the relevant APIs. Keep all state meaning available without movement. Do not delay clicks or completed content until an animation ends.

## 9. Frontend architecture and component strategy

Adopt compatible shadcn primitives incrementally: Button, Input, Textarea, Label, Dialog, AlertDialog, Sheet, Tabs, DropdownMenu, Tooltip, RadioGroup, Separator, Skeleton and Command where useful. Add only components actually used. A simple custom layout can be more appropriate than importing a full Sidebar block. Component source must be reviewed; a registry origin is not proof of accessibility.

Suggested organization (adapt filenames to actual responsibilities):

```text
frontend/
  app/                         # retain existing route URLs
  components/
    ui/                        # owned primitives
    shell/                     # AppShell, navigation, header, repo switcher
    workspace/                 # chat, composer, file pane, evidence inspector
    architecture/              # toolbar, canvas nodes, node details/list
    trace/                     # step list/details
    practice/                  # study list, viva session, evaluation
    shared/                    # Markdown, CodeBlock, EmptyState, ErrorState
  context/                     # repository/theme/search session owners
  hooks/                       # domain hooks where behavior is reused
  lib/
    api.ts                     # retain transport contracts
    api-types.ts               # narrow DTOs and normalizers
    storage.ts                 # preserve keys / explicit migrations
    utils.ts                   # cn() using existing clsx + tailwind-merge
```

Retire GlassCard after all uses migrate. Replace it with purpose-specific pane and content structures, not another universal animated wrapper. Use `cn()` to resolve class conflicts instead of concatenating competing padding/color classes.

State ownership:

- ProjectProvider owns registry loading/error/selection. A switch has a single explicit transition.
- A repository workspace session owns messages, draft, selected file and attachment. At minimum, clear these safely on switch; preferred behavior keeps a separate in-memory session per repository. Do not persist image blobs/API keys into conversation history.
- One global search provider owns open/close and result selection. Normalize file/symbol/chunk results into a discriminated union, then dispatch a selected-context intent to Workspace.
- Async operations carry repository ID plus operation/request ID. Reject every stale update, including `finally`, so request A cannot turn off B's busy state. Clear pending stream timers on abort and update the intended assistant message by ID instead of blindly replacing the last message.
- Cache graph/explanation/trace results by repository and parameters. Explicit refresh bypasses cache. Avoid gratuitous new global-state dependencies; introduce a query library only if its value and compatibility are demonstrated.
- Keep browser-only APIs in client components; keep the root layout as narrow as possible. Preserve dynamic canvas loading, memoized messages and batched token updates.

## 10. Integration boundaries: achievable now vs separate work

| Proposal | Frontend-only baseline | Separate backend dependency |
|---|---|---|
| Repository processing feedback | Indeterminate status and elapsed time, truthful errors | Immediate job ID/secure ownership association and actual stage events for reliable live progress |
| Stop ingestion | Stop waiting only if carefully labelled; avoid promising task cancellation | Ingestion must check a cooperative cancel signal and stop/clean up work |
| Source inspector | Existing path, chunk ID and snippet from citations/search/trace | Full file content and exact line navigation need a guarded source-read contract |
| Model details | Remove hardcoded model names from primary UI | Expose safe runtime configuration if details are required |
| Cross-device history | Per-repo current-browser session | Server persistence/account model would be new product scope |
| Shareable answers | Copy text/source list | Durable share links need storage and access-control decisions |

Preserve `NEXT_PUBLIC_API_URL`, `X-Session-Id`, `X-Gemini-API-Key`, JSON/FormData distinctions, POST SSE handling, abort signals and citation payloads. Keep the static-export deployment. Do not introduce Server Actions, server-only route handlers, a new auth system or a different AI backend as part of the visual redesign.

## 11. Implementation work packages

Effort ranges are planning estimates for focused engineering/design work, not elapsed AI execution promises. They exclude optional backend additions. Rough total: **13–20 person-days**, with functional repair and a complete Workspace vertical slice first.

| Phase | Scope / principal files | Dependency | Done when | Estimate |
|---|---|---|---|---|
| 0. Baseline | Capture six routes, inventory states/API DTOs, run existing build/typecheck; record current defects and asset sizes | None | Reproducible baseline and truthful fixture set; pre-existing failures recorded | 0.5–1 day |
| 1. Behavioral foundation | ProjectContext, page chat lifecycle, search/file filter, request guards and typed error handling | 0 | Search works; repo A/B race tests pass; service error is not empty state | 2–3 days |
| 2. System and shell | globals.css, Tailwind tokens, fonts, ui primitives, layout/Header/Sidebar/ThemeContext | 0; integrate 1 | Responsive shell, keyboard dialogs, both themes; all routes reachable | 2–3 days |
| 3. Workspace vertical slice | First-run setup, repository switcher, FileExplorer, composer, Markdown, source inspector | 1,2 | Add/select → ask → stop/retry → inspect evidence works at desktop/mobile | 3–4 days |
| 4. Exploration tools | Architecture canvas/toolbars/list, tracer step UI, audience reading view | 1–3 | Each mode preserves real contracts, async ownership and readable layout | 2–3 days |
| 5. Practice and settings | Split viva UI responsibilities, voice/text states, progress/exports, credential forms | 1–3 | Failed evaluation retains answer; statuses/exports work; tested vs saved is clear | 2–3 days |
| 6. Finish and delivery | Motion, responsive/theme verification, meaningful tests, performance comparison, documentation | All | Evidence-based acceptance checklist with no critical unresolved task blockers | 1.5–3 days |

Do not polish all six pages independently before the shell and shared evidence/Markdown/state components work. Complete every phase; the first vertical slice is a review milestone, not permission to leave secondary routes in the old style.

## 12. Verification and release gates

### Functional acceptance

1. GitHub/ZIP success and failure preserve useful input; concurrent ingestion cannot produce ambiguous active state.
2. File filtering handles nested matches, no matches and clear; selection never overwrites an existing chat draft.
3. Cmd/Ctrl+K works from all six routes. Mouse/Enter chooses the same normalized target; selection does useful work and Escape returns focus.
4. Switch A → B during chat, architecture, trace, explanation and viva work. A's output/error/finally cannot change B; B requests contain no A conversation/context.
5. Stop streaming preserves delivered text and releases pending timers; retry is explicit and does not duplicate user messages.
6. Backend unavailable, valid empty registry, rejected key, failed search and zero results are distinguishable with recovery actions.
7. Citations show only real available evidence, no invented file contents or line numbers.
8. Microphone unsupported/denied/error paths keep text answering functional; evaluation failure retains draft; duplicate submissions are prevented.
9. Practice statuses, question filtering, Markdown download and print layout work; summary uses actual session data.
10. Existing static export builds and all six direct-route URLs refresh correctly through the production serving arrangement.

Use deterministic mocked API fixtures for interface states, labelled as fixtures. Separately run a small real integration smoke test when an authorized backend/key is available. Do not equate mocked output with verified model/API behavior.

### Responsive, accessibility and visual gate

- Check 320, 390, 768, 1024, 1440 and 1920px widths, plus 200% zoom/reflow. No document-level horizontal overflow. Long paths/URLs remain accessible; code scrolls locally.
- Inspect every route in both themes and relevant empty/loading/error/ready states. Include deep paths, long repository names, long answers, wide code/table output and large representative file trees.
- Keyboard-only traversal reaches every actionable element; dialogs trap/restore focus; Escape, selected tabs and active navigation behave correctly. Add skip navigation and named main/navigation regions.
- Target WCAG 2.2 AA. Normal text contrast at least 4.5:1, large text at least 3:1, meaningful non-text control/focus contrast at least 3:1. Verify actual pairs, not palette labels. The [W3C target-size criterion](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) defines the 24px minimum/spacing exceptions; the larger 44px touch target here is a product comfort target, not a claim that AA universally requires it.
- Use accessible names for icon buttons and programmatic labels/errors for fields. Status must not rely on color alone. Announce generation start/end/error without reading each token.
- Reduced motion suppresses decorative transforms/pulses while preserving feedback. Focus/selection remains visible in forced-colors/high-contrast settings where supported.
- Capture an initial batched review, fix the discovered issues together, then capture confirmation. Do not keep inventing micro-polish work after the acceptance matrix is satisfied.

### Frontend performance and build gate

Run a baseline and after measurement under the same conditions. Targets: LCP ≤2.5s, INP ≤200ms and CLS ≤0.1 as production field goals, not guaranteed lab scores. AI response latency is measured separately from client input responsiveness. Track compressed route JS and graph chunk size; any >10% baseline increase needs an explicit reason. A trace/graph dependency should not silently move into the Workspace bundle.

Measure representative large-tree filtering and long streaming conversations. Preserve 40ms-class stream batching or improve it with evidence. Avoid repeated full Markdown parsing/layout work when profiling shows a problem. Add virtualization only when measured data sizes justify the complexity.

Run TypeScript, production build, configured lint, and behavior tests for actual failure modes. If `next lint` requests setup or a baseline dependency fails, report/fix tooling deliberately rather than claiming it passed. Test the production export through the existing serving setup. No framework migration is required to meet this design brief.

## 13. Handoff checklist

- Updated source and a concise changed-files summary.
- Token-bearing DESIGN.md, component conventions and state ownership notes reflecting the implementation.
- Verified desktop/mobile light/dark screenshots with state and fixture labels.
- Test/build results, unresolved issues and explicit backend limitations.
- No dead controls, fabricated repository data, made-up scores, false job cancellation or claims of guaranteed AI accuracy.
- Preserve the developer's existing work; this checkout had no accessible `.git` repository during this audit, so do not assume a clean branch or usable git rollback.

Questions skipped: the user requested a complete audit, proposed scheme, implementation plan and IDE handoff. The assumptions above are explicit so the proposal can be reviewed without blocking delivery on a taste questionnaire.

## 14. Browser evidence

Browser inspection used the existing development app at `http://localhost:3000`, an isolated headless Chrome session, and a synthetic repository fixture for active-project views. No real repository was ingested and no AI generation was triggered. Captures were opened and visually inspected; theme captures were corrected to select Dark explicitly and allow entrance transitions to settle.

| Evidence | Observation |
|---|---|
| `output/playwright/current-desktop-empty.png` | Light first-run view, 1440×1000. Setup banner and repeated no-project/key status dominate the composition. |
| `output/playwright/current-mobile-empty.png` | 390×844 viewport. Measured document width **608px**, sidebar **300px**. Main content is severely squeezed/clipped and flows into a narrow vertical strip. UX-06 is browser-confirmed. |
| `output/playwright/current-dark-workspace-fixture.png` | Active synthetic repository, settled dark Workspace. Permanent ingestion banner competes with chat; metadata repeats in header/sidebar; small text and large container padding reduce useful density. |
| `output/playwright/current-dark-audience-fixture.png` | Dark Explain view. Page title, audience names and empty-state heading remain dark against dark surfaces and are visibly difficult to read. UX-11 is visually confirmed; no numerical contrast ratio was measured. |
| `output/playwright/current-dark-architecture-fixture.png` | Not-generated architecture state. Heavy explanatory header and wrapped view labels compete with the main canvas entry action. Generated graph layout was not exercised. |
| `output/playwright/current-dark-tracer-fixture.png` | Initial trace form. Nearly the entire remaining viewport is empty, with no contextual starter actions. Generated trace results were not exercised. |
| `output/playwright/current-dark-viva-fixture.png` | Initial Study tab. Repeated large setup container and gradient generation action. Live recording/evaluation was not exercised. |
| `output/playwright/current-dark-settings-fixture.png` | Dark Settings view. Appearance and key controls render, but implementation/model cards add considerable visual and cognitive weight. No real key was entered. |

Browser interaction checks reproduced UX-01 and UX-02: entering `definitely-no-matching-file` left `api.ts` visible; selecting a mocked `src/api.ts` search result closed the dialog with an empty composer and no selected-file context control. These findings also have direct source evidence. Fixtures test interface behavior, not backend search accuracy.

An additional capture, `current-desktop-workspace-fixture.png`, is a settled desktop Workspace in the session's persisted dark theme; its filename does not imply light mode. The console also recorded `/icon.png` returning HTTP 500 in this development environment. Reproduce and investigate that asset issue during baseline work; this audit did not establish its root cause.

Not verified end to end: real ingestion, live model generation, generated graph/output layouts, speech permissions/recording, race-condition reproduction, full keyboard/screen-reader coverage, production build, Lighthouse or field performance. Those remain explicit implementation acceptance checks. The repository-switch/request-race findings are source-confirmed. Automated Impeccable detection was unavailable; there is no detector overlay or automated pass claim.
