# Local review: accounts, voice and proactive pages

The approved integration is published through `feat/ui-accounts-voice-proactive`
for `integration/intent-enhancement`. This document also describes the local
review harness, which is not a Cloud deployment entry point. Publishing the code
does not wire the production cookie transport or deploy a scheduler.

## Sources checked on 2026-11-10

- Reviewed proactive UI baseline: `3f60a8e` (backend B base `073b086`).
- Account backend integration: `integration/account-p5-dev` at `025134a`.
- Account UI: `feat/account-ui` at `388819c`, including the published P1–P4 merges.
- Voice UI: `feat/voice-input-ui` at `01bc464`.
- Voice backend: `feat/voice-input-backend` at `f3baac9`.
- Repository branch tips and commits since 2026-11-05 were fetched and inspected.

## Integration behavior

- Preserve the reviewed navigation, center composer, dialog sizing and three palettes.
- Route keyboard and reviewed voice text through the same P4 persistent submission boundary.
- A recording only produces an editable draft. It never directly executes an Agent task.
- Bind recordings, drafts and callbacks to the originating chat and verified login.
- Preserve drafts when switching chats. Clear drafts, selected evidence, proactive form
  state and task views on logout or identity change.
- Reuse an untouched empty chat. A recording or editable voice draft is real activity.
- Bind proactive pages to the server-verified account workspace; never fall back to
  the anonymous demo identity when signed in.
- A new proactive reply can open review once after saving; reading history does not
  reopen review or resubmit work. Return navigation opens the authorized UUID chat.
- Restore the approved evidence CSV export for the currently authorized reply.
  Revalidate the account before rendering its download control.
- Save all four Quick Workflows through P4's normal begin/commit transaction.
  Only canonical workflow parameters are accepted; the server computes the answer.
  Store answer, evidence and unchanged contextual Agent state together. A repeated
  request ID reuses its completion receipt and never executes the business tool twice.
- Restore the existing Start a fresh request control for account chats. Its server-owned
  reset is committed transactionally and survives reopening; its internal receipt pair
  is not shown as a new user/assistant exchange. Existing visible history is retained.

## Approved UI audit

Native-composer follow-up (2026-11-11): restore the earlier Workspace/footer
spacing. Keep the navigation section's total height, distribute its four rows
evenly and balance the heading/divider clearance (about 13 px at 1366x768).
Remove the separate transcription editor and its Re-record, Cancel re-record,
Send reviewed text and Discard controls. A successful transcription now prefills
the original native chat input without submitting a business request. The user
can edit/delete it or record additional speech, then use the ordinary Enter/send
action. A new recording appends to the current input, including manual edits;
deleted transcripts are not resurrected. Preserve unsent composer text per chat,
reject stale recording/input events and clear account drafts on identity change.
The zero-height native component syncs input events in a fragment; it does not
write to chat storage or run the Agent while typing. Related checks: 233 passed.
Browser checks covered draft restoration after visiting Watches & Alerts, clearing
the input, empty-chat reuse, navigation spacing and restored footer visibility.
Speech recognition was mocked in automated checks; live microphone accuracy
remains a user check.

Follow-up readiness and layout changes (2026-11-11): a verified login initializes
one reusable empty conversation, so both typing and recording are enabled without
clicking New conversation. Initialization runs once per login; it probes existing
authorized empty chats before creating one, and does not auto-retry an uncertain
creation. Deleting the selected chat returns to a usable blank conversation.
Remove the start-chat notice and Microphone help expander. Compact navigation rows
and footer spacing to allocate more height to Recent Chats while keeping the account
controls clear of the bottom edge. Rename/Delete reuse the existing workflow-dialog
drag, resize and content-fit component. Their regular and form-submit buttons follow
the green, silver and black palettes. Validation: 175 related tests passed; browser
checks covered immediate typing/audio readiness, 1366x768 footer visibility, white
and black submit colors, pointer dragging and horizontal/vertical resizing.

Follow-up recent-chat fixes on 2026-11-10: before creating a private chat, read the
authorized existing snapshots and prefer an untouched empty conversation, including
after a fresh login or navigation away from Co-Pilot. Voice drafts and pending sends
exclude a thread from reuse. Do not delete earlier duplicate records automatically.
Move compact-history Rename/Delete into each row's ellipsis menu. Render the editing
and deletion-confirmation dialog outside the sidebar scroll/clip container and validate
the current session and target ownership on every operation. The order-ID parser and
clarification policy were inspected for the bare `058` example, not changed in this fix.
Account UI validation: 141 passed. Palette/scroll/title validation: 22 passed.

The user's version at `3f60a8e` is authoritative for existing UI. The incoming branches
contribute account and voice capabilities, not replacements for the existing design.

- Day/night/green palette definitions, result cards, copying, dialog geometry/dragging,
  workflow docking, answer scrolling and proactive page layout remain from that baseline.
- Preserve the three independent surfaces, centered bottom composer, sidebar controls,
  quick-workflow launchers and the existing review/return journeys.
- Restore `＋ New conversation`; repeated clicks reuse the untouched private conversation,
  including after visiting another navigation page.
- Retain first-exchange recent-chat summaries and unabridged hover details. Feasibility
  keeps quantity/category in the short label and the delivery date in hover text.
  Explicit account Rename Chat titles are respected.
- Restore the first-line, right-aligned, borderless View Evidence action and original
  full-analysis/copy rendering. Keep account-specific widget identities behind the
  original styling hooks, including the workflow Run button.
- Use host-native user bubbles so day/night text follows the approved palette. Show
  the new question before the original thinking-ring animation and Agent execution.
- Quick Workflows produce ordinary saved user/assistant exchanges, not preview cards.
- Retain ordinary history translation, voice draft review and account ownership checks;
  clear account-specific caches and UI state when the verified identity changes.

## Validation on 2026-11-10

453 related tests passed, with 7 database-dependent tests skipped, covering account UI,
P4 persistence/receipts/state restoration, all four guided workflows, rejected identities,
unknown save outcomes, session/identity changes during sidebar
reads, reviewed voice and draft isolation, new-chat reuse, proactive review and return
navigation, existing chat integration, palettes and workspace scrolling. Ruff checks
and Git whitespace checks passed. Browser checks covered login/logout, separate demo
accounts, the personal chat list, microphone entry and the three palettes. Live audio
recognition and production browser-cookie persistence were not claimed by these tests.
Supabase was not contacted for this UI review. Follow-up checks covered 167 account,
speech and chat scenarios; the final account-integration rerun passed all 38 tests after
the empty-account translation-cache adjustment. Ruff and whitespace checks also passed.

## Local visual review entry point

Run `tools/review_accounts_voice.py` with `SWEATERCO_LOCAL_REVIEW=1` and
`--server.address 127.0.0.1`. The harness refuses non-loopback listening.

The explicitly labeled account test mode uses actual P1/P2/P4 services with test-only
in-memory storage and transport. It does not connect to Supabase or set production
browser cookies. Seeded accounts are `preview_alice` and `preview_bob`, both with the
public, disposable password `SweaterCo preview account 2026!`. Registration, personal
chat operations and logout can be reviewed. Refresh returns to sign-in; server restart
clears the in-memory users and chat data. These are not Cloud persistence results.

The ignored local `.env` supplies model settings if configured. Microphone transcription
uses the incoming SpeechService and requires a working API key and transcription model.
Mock tests do not establish live recognition quality. Never commit `.env` or local secrets.

## Production boundary retained from P5

The incoming P5 branch has no production `BrowserSession` transport. A deployment
entry point must inject P4's real backend and an approved server transport applying
P2's HttpOnly/Secure/SameSite cookie instructions. Supabase persistence, login renewal,
refresh/re-login recovery and two-account browser isolation must then be validated
against that deployment. This integration does not replace the missing transport with
JavaScript cookies, URL tokens or a username stored in session state.
