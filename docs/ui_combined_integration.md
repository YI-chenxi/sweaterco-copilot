# Combined UI handoff

## Branch and source revisions

- Review branch: `feat/ui-combined`.
- Target: `feat/streamlit-agent-ui`; the combined result can be proposed to
  `integration/intent-enhancement` after shared UI review.
- Approved appearance source: `623762f` (`feat/streamlit-ui-chat-layout`).
- Person B source: `944f47c` (`feat/ui-person-b`).
- Local integration merge: `8d8d143`, retaining both source histories.
- PR #10 remains separate until the combined change is merged.

## Delivered interface

The approved three-panel structure is preserved. Recent Chats scrolls independently
and uses compact first-exchange titles, with quantity/category retained for
feasibility and dates available on hover. User questions appear on the right;
assistant answers appear on the left. The native composer stays at the bottom of
the center surface. Questions render before the thinking indicator.

WORKSPACE contains AI Configure, the global language selector and three small
appearance controls. Daily mode uses frosted silver/white, Eye comfort mode keeps
the approved mint/teal palette, and Night mode uses neutral black/graphite. Swatches
are 14 px with a thin selection ring and translated rounded hover labels. Theme
changes preserve messages, task state, selected evidence and saved workflow inputs.

The center and analysis rail have independent scrolling. The left collapse control
is always visible. The analysis heading has a compact Hide control; its reopen
button sits at the right of the center area. The follow-answer toggle is aligned
with its label. Streamlit Deploy/menu chrome and redundant result/footer labels
are hidden.

## Workflows and answer controls

All four guided workflows open parameter dialogs. Drag the title to move and the
edges/corners to resize. There is no size toolbar or visible resize glyph. Geometry
is constrained to the viewport and retained in browser session storage; only
numeric window geometry is stored there. Native Escape and the top-right 44 px
close target preserve saved parameters. Dialogs do not tint or dim the page.

Night dialogs and Run use solid graphite; the Daily Run control is white. Selected
workflow cards use theme-specific gradients rather than the generic primary fill.

Each answer offers a compact borderless View Evidence action beside its conclusion,
key metrics, optional full analysis and a one-click copy icon at the expanded
analysis's upper right. Copy does not open a second text window. Evidence selection
uses stable answer IDs and remains local to the conversation. Scroll following can
be disabled; manual selection is respected until the next intentional chat scroll.

The evidence grid, toolbar, menus and scrollbars follow the selected theme. Night
cell styling changes presentation only: source values, CSV exports and backend
results remain intact. Day and Night send controls use solid high-contrast colours
and a rounded-line arrow.

## Language and state

One `ui_language` key controls UI labels, guided replies and translated history.
The original backend replies and agent sessions are never translated in place.
Deterministic workflow text is localized locally; free-text history can use the
configured model and a separate cache. Native form changes persist per thread.

History and appearance are session-local. Conversation history does not survive a
full browser refresh or server restart. Dialog geometry storage is independent of
business task parameters.

## Runtime and validation

Requires Streamlit `>=1.66.1,<2` for native dialogs, fragments and v2 components.
537 automated tests passed on the final approved version. Code-format,
JavaScript syntax and whitespace checks also passed before publication. Tests use mocked model calls and real
local business tools; automated regressions do not measure live-model accuracy.

Browser checks cover the four guided dialogs, manual movement/resize, close/reopen,
copy, evidence selection, panel controls, language-menu highlights, native table
scrollbars, enabled send states and all three palettes. The user approved the final
local appearance before requesting publication.
