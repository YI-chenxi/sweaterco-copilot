# Local review: accounts, voice and proactive pages

This is a local integration branch, not a Cloud deployment. No remote branches
or pull requests are updated by the review workflow.

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
- Retain the P5 restriction on private Evidence CSV export.

## Validation on 2026-11-10

243 related tests passed, covering account UI, session/identity changes during sidebar
reads, reviewed voice and draft isolation, new-chat reuse, proactive review and return
navigation, existing chat integration, palettes and workspace scrolling. Ruff checks
and Git whitespace checks passed. Browser checks covered login/logout, separate demo
accounts, the personal chat list, microphone entry and the three palettes. Live audio
recognition and production browser-cookie persistence were not claimed by these tests.

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
