# Chat layout and history navigation

This UI branch includes the integration baseline at
`cbe10328b57b9739241f9ad03f226f362e740547` (PR #8), synchronized through PR #9.

## Current behavior

- The desktop workspace has three independent areas. A near-white hydrogel
  center surface contains both the conversation and its native inline composer.
  The outer frame stays fixed; conversation content and the right detail rail
  scroll independently. Narrow screens stack the detail rail below the center.
- The boxed welcome header and four separate workflow shortcuts retain their
  structure. The mint/teal theme uses 18 px user bubbles, open assistant replies,
  softly beveled shortcuts and flat pale-green metrics. The subtitle reads
  "Your factory, at a glance." The simulated-data date card remains visible.
- The native composer provides an embedded send button, a continuous rounded
  border, a clear focus ring and a white arrow on the enabled green button.
  Enter submits; Shift+Enter inserts a newline. A submitted question appears
  before an assistant-side "I'm thinking..." indicator and model execution.
  Failed requests keep their question and allow another turn.
- Recent Chats lists every conversation in the current session in its own
  scroll area. Labels derive from the first exchange without extra model calls
  and remain stable across follow-ups. Guided feasibility keeps quantity and
  category (for example, "800 TOPS feasibility"); its date remains in the full
  hover text. Legacy "Structured workflow" suffixes are removed from display.
- Sidebar width remains resizable within 280-430 px on desktop. Navigation and
  the AI Configure status line remain fixed while only Recent Chats scrolls.
  Configure shows configured/missing-key states directly. Reduced brand spacing
  and the global English/Chinese control sits below Configure in the reserved area.
  Collapse/reopen controls share their top edge; collapse stays inside the sidebar.
- The right rail combines white evidence cards, pale-green context/assumption
  cards and warm warning cards. Expanders have one outer rounded border.
  Evidence export, warnings, assumptions and business values retain their data.
- Streamlit Deploy/menu chrome, redundant response-origin labels, the API hint
  and the bottom snapshot footer/divider are removed from the product view.

## Compatibility and scope

The combined UI requires Streamlit >=1.66.1 (the validated runtime), including
its dialog, fragment, native chat and component APIs. Business rules, backend
contracts and model configuration logic remain unchanged. The shared
`ui_language` control updates presentation without rewriting saved results.

History remains session-local and does not survive a full browser refresh or
server restart. Mock-model tests do not measure live-model accuracy.

## Verification

- 532 automated tests passed, including chat submission, missing configuration,
  recovery after failure, conversation isolation, reopening/resuming old chats,
  first-exchange title stability, bilingual workflow labels, stable evidence
  selection and guided feasibility labels with hover dates.
- Ruff checks, Python formatting checks and `git diff --check` passed.
- Browser checks covered the real 800 TOPS guided workflow, hover date text,
  single expander borders, fixed desktop frames/internal scrolling, sidebar
  collapse/reopen alignment and the native send/focus states.

Run from the repository root:

```powershell
python -m streamlit run app/ui/streamlit_app.py
python -m pytest -q
```

## Combined UI behavior

The global language control, saved workflow parameter dialogs, summary/full
analysis controls, copying and per-answer evidence selection come from personB.
They share A's approved three-panel material, native footer composer and complete
history list. Evidence follows the answer being read; users can turn following
off or select an answer explicitly. Original replies, evidence exports and task
memory are preserved. The language view cache is separate from business data.
Only the visible first 15 evidence descriptions are prepared for display
translation; full CSV exports retain all original source records.

Browser verification additionally covered an unsent native-input draft across
language and panel-visibility changes, and scroll-driven switching between the
ORD-013 and ORD-057 evidence payloads without rerunning the whole conversation.
