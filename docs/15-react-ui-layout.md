# React UI — Application Shell & Layout

Status: **wired to real endpoints, actively being built out.** The
mock-data phase described below is complete and historical — every tab
except Analytics (still a placeholder, per "Tabs" below) now calls the real
API through `frontend/src/api/*.ts`, not `frontend/src/mocks/`. `test_ui.html`
still exists as a secondary reference dashboard, but this React UI is now
the primary, fully-wired interface for Main (data pipeline), Planning (the
full New Model 2 flow), and Configuration (priority-bucket CRUD).

## Stack

- **Vite + React** — new `frontend/` folder at project root, sibling to
  `backend/`. Vite chosen over Next.js/CRA: this is an internal SPA
  dashboard with no need for server rendering or file-based routing, and
  Vite's dev server / build are the fastest of the three, which matters
  given Sarath's explicit priority: "smooth... no lag in loading."
- **Tailwind CSS**, hand-built components — no component library (shadcn/
  MUI/etc.). Decision driver (Sarath, this task): "decent UI, not too
  fancy... smooth, no lag." A component library adds bundle weight and a
  more opinionated default look; Tailwind + small hand-rolled primitives
  keeps the bundle light and the look fully controllable, and fits
  CLAUDE.md's "keep code minimal, use libraries, don't hand-roll" without
  pulling in a heavy dependency tree for a UI this size.
- Icons: `lucide-react` (small, tree-shakeable, no extra look-and-feel
  baggage).

## Mock-data phase — historical, now complete

CLAUDE.md rule 7 says frontend components must fetch through the API
service layer, never embed sample/mock data, once real endpoints exist.
Building pure layout first, before wiring to those endpoints, was a
deliberate, temporary exception Sarath asked for explicitly (parallel-track
UI work while backend testing continued) — not a silent violation of the
rule, and it's now closed out: every component fetches through
`frontend/src/api/*.ts` against the real backend. If a `frontend/src/mocks/`
module or a `// TEMP MOCK` comment is still found anywhere, it's stale
leftover, not an active exception — flag it rather than assuming it's still
load-bearing.

## Application shell

- **Left sidebar** — collapsible/expandable (icon-only when collapsed,
  labelled when expanded, same interaction pattern as Claude Desktop/
  ChatGPT's left nav). Contains the 4 primary nav items (tabs, below).
  Below the nav items, pinned to the bottom of the sidebar: account name,
  role, and a Logout control. This account block is shell-level chrome,
  not part of any individual tab.
- **Main content area** — renders whichever tab is active. Tab content
  switching is client-side (no full page reload).

## Tabs

Only tab 2's internal layout is specified in detail below — tabs 1, 3, 4
are placeholders at the layout level for now; their internals are separate,
later work (per Sarath's explicit instruction not to design "tabs inside
details" yet, this task is shell + tab-2 structure only).

### Tab 1 — Data Pipeline

- Header: tool name + an Excel upload control.
- Below it: a history card — one entry per past upload, showing the Excel
  file name, the upload timestamp (date + time), and the month/year that
  upload represented.
- Below that: a Master Data grid card (`MasterDataGrid.tsx`) — one row per
  active vendor, one column per real Excel column in the file's own order,
  reloading after every upload/revert. React equivalent of the grid that
  previously only existed in `test_ui.html` (docs/07, docs/11).

### Tab 2 — Planning (New Model 2)

Top-to-bottom, in this order:

1. **Four cards in a row**: Calendar (month + year picker) → Minimum Funds
   Required → Available Funds (input) → Funds Left. Matches the four-card
   flow already built per `docs/14-new-model-2.md`.
2. **One horizontal bar**, directly under the four cards, containing both
   of the following side by side (not stacked):
   - A vendor name search/filter control — collapsed to a simple icon by
     default, expands on hover to reveal the full filter set.
   - Two buttons: **Generate Plan / Regenerate Plan** (single button,
     label toggles by state, matching existing `test_ui.html` convention)
     and **Finalize Plan**.
3. **Planning table**, below the bar.
4. **Payments table**, below the Planning table.

### Tab 3 — Analytics

Placeholder only. Sarath will provide a reference for what this shows.

### Tab 4 — Configuration

Placeholder only, role-gated — visible/usable only for allowed roles.
**Open item, flagged, not decided today**: this project has no
role/auth/user model defined anywhere yet (not in the DB shape in
`docs/12-database.md`, not in `docs/9-open-questions.md`). The layout
phase mocks a role value to drive the visual gating; a real role/permission
model is separate backend work to be scoped later, before this tab does
anything beyond a client-side visual gate.

## Non-goals of this task

No API calls, no real data, no routing library beyond simple client-side
tab state, no auth. Purely the shell and tab-2 structural layout, with
placeholder content elsewhere.
