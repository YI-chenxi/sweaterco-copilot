# UI integration review

## Source revisions

- Shared UI base: `8249bdb` (`feat/streamlit-agent-ui`).
- A: `623762f` (`feat/streamlit-ui-chat-layout`), user-approved appearance.
- B: `944f47c` (`feat/ui-person-b`), bilingual workflows/evidence navigation.
- Integration branch: `feat/ui-combined`. Both source branches remain intact.

## Conflict resolution

The main UI and stylesheet conflicted. The merged version preserves A's mint
palette, white hydrogel center, 18 px user bubbles, flat metrics, native composer,
short stable history labels and independent scrolling. B's language controls,
parameter persistence/dialogs, full-analysis/copy actions and evidence ownership
are connected to those containers. Old text-area/send controls, eight-chat
limits, source banners and snapshot footer are not restored.

The language widget lives below AI Configure. Both English and Chinese generated
feasibility titles keep quantity/category while hover text retains the date.
Saved backend replies and conversation task state are never translated in place.
B's tests that addressed the replaced text area now exercise the native composer;
actual unsent-draft preservation is checked in the browser because AppTest's
chat-input `set_value().run()` submits rather than simulates an unsent draft.

## Validation and rollout

532 tests, Ruff, formatting and whitespace checks pass on Streamlit 1.66.1.
Additional integration coverage switches language while feasibility waits for a
date, then resumes it and returns to earlier evidence without changing task state.
Browser checks cover language/draft persistence, old chats, guided 800 TOPS,
copy/full-analysis controls, panel visibility and scroll-based evidence follow.
History remains session-local; refresh persistence is not implemented.

The combined version is held locally for visual acceptance. After acceptance,
create a PR from `feat/ui-combined` to `feat/streamlit-agent-ui`. Only after that
shared UI version is checked should it be proposed to
`integration/intent-enhancement`. PR #10 is retained until the combined PR lands.
