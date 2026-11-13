# SweaterCo Operations Co-Pilot

SweaterCo Operations Co-Pilot is a bilingual, evidence-first assistant for a fast-response garment factory general manager. It combines natural-language chat, deterministic business analysis, guided workflows, optional private accounts, voice transcription, and reviewed proactive actions in one Streamlit workspace.

This repository is the NUS DSS5105 Capstone Project, Track 1.

## What the App Does

The current integrated application supports four core business workflows:

| Workflow | Example request | Result |
| --- | --- | --- |
| Order Status | `How is order ORD-057?` | Current status, production progress, delivery risk, warnings, and source evidence |
| Production Health | `Is Assembly operating normally?` | Stage-level production assessment with supporting production records |
| Morning Briefing | `Give me the top 5 risks today.` | Ranked operational risks and the evidence behind each finding |
| Order Feasibility | `Can we accept 800 TOPS by 2026-04-25?` | Capacity-based feasibility, constraints, assumptions, and workshop evidence |

The same workflows are available through structured Quick Workflows. Structured workflows do not need an LLM to identify the task or extract parameters.

Additional integrated capabilities include:

- English and Chinese questions, answers, interface labels, and clarification prompts.
- Multi-turn slot completion and up to two tasks in one natural-language request.
- Separate conversation state for each chat.
- Warnings, assumptions, metrics, technical trace, and downloadable evidence.
- Editable speech-to-text drafts that never execute a task until the user sends them.
- Green, day, and night appearance modes.
- Optional pre-provisioned accounts with PostgreSQL-backed private conversations.
- Immediate and scheduled briefings.
- Order watches, stored checks, and alerts.
- Proposal, review, confirm, reject, idempotency, and audit flows for mutating proactive actions.

## Design Principles

The LLM understands language; it does not calculate business facts.

```text
Typed or reviewed voice input
        |
        v
Intent and slot extraction
        |
        v
Validation and multi-turn planning
        |
        v
Deterministic A1 retrieval and A2 judgement tools
        |
        v
AgentReply: answer + evidence + warnings + assumptions + metadata
        |
        v
Streamlit chat, analysis panel, private history, or proactive review
```

Order records, production totals, risk scores, and feasibility calculations come from local deterministic tools. The LLM is used for intent understanding and optional answer polishing. The application does not send the factory CSV datasets to the model for business calculation.

## Data Semantics

The included factory dataset is a simulated snapshot with a business reference date of **1 April 2026**. It is not live factory telemetry.

The fixed reference date is intentional: it keeps order status, overdue logic, and feasibility results reproducible against the supplied dataset. Scheduled proactive services also have runtime timestamps, so the UI must not present the snapshot as current real-world factory data.

Source files:

- `data/orders.csv`
- `data/production_log.csv`
- `data/workshops.csv`
- `data/data_dictionary.md`

## Quick Start on Windows

Requirements:

- Windows PowerShell
- Python 3.10 or newer
- An OpenAI API key for natural-language chat and speech transcription

From the repository root, run:

```powershell
.\setup.cmd
.\start.cmd
```

For the complete local Sprint 3 demo with sign-in, private per-user chats, Briefing schedules, Watches, and reviewed Actions without PostgreSQL setup, run:

```powershell
.\demo.cmd
```

This starts a loopback-only local account test mode with three disposable users:

```text
Username: preview_alice, preview_bob, or Ellie
Password: sweaterco
```

Account and chat data remain available while the demo server is running and reset when it stops. Proactive tasks use `runtime_data/sprint3-demo.sqlite3`. This mode listens only on `127.0.0.1` and must not be used as a public deployment.

The setup command:

1. Creates or reuses `.venv`.
2. Installs the validated versions in `requirements-lock.txt`.
3. Creates `.env` from `.env.example` only when `.env` does not already exist.
4. Preserves existing secrets.
5. Runs a safe environment check that reports configuration status without printing secret values.

The start command validates the environment again and opens the app at:

```text
http://127.0.0.1:8501
```

PowerShell users can call the underlying scripts directly:

```powershell
.\setup.ps1
.\start.ps1
```

Use a different port or prevent automatic browser opening with:

```powershell
.\start.ps1 -Port 8502 -Headless
```

Run the environment check without starting the app:

```powershell
.\.venv\Scripts\python.exe tools\check_environment.py
```

## Model Configuration

Local model settings belong in `.env`, which is excluded from Git:

```dotenv
MODEL_PROVIDER=openai
OPENAI_MODEL=gpt-6-luna
OPENAI_API_KEY=your_own_api_key_here
OPENAI_TIMEOUT_SECONDS=30
SPEECH_MODEL=gpt-transcribe
SPEECH_TIMEOUT_SECONDS=30
LOG_LEVEL=INFO
```

Never place real credentials in `.env.example`, source code, tests, logs, screenshots, or commits.

Without a valid API key:

- Structured Quick Workflows can still run.
- Natural-language intent parsing, answer polishing, and speech transcription are unavailable or safely degraded.

## Runtime Modes

### Anonymous Mode

Anonymous mode is the default when Streamlit account secrets are absent.

- Core chat and Quick Workflows are available.
- Conversations live in the current Streamlit session.
- History is not guaranteed to survive a browser refresh or server restart.
- Account-scoped Watches and Actions are unavailable unless local proactive demo mode is configured.

### Local Proactive Demo Mode

For a clearly labelled local demonstration only, add the following values to `.env`:

```dotenv
PROACTIVE_DEMO_MODE=true
PROACTIVE_DEMO_WORKSPACE=local-demo
PROACTIVE_DB_PATH=runtime_data/proactive.sqlite3
```

This uses a local demo identity and SQLite persistence. Do not enable this mode on a public deployment.

### Login-Only PostgreSQL Mode

The integrated Streamlit entry point can use pre-provisioned accounts and PostgreSQL-backed private chats. Create `.streamlit/secrets.toml` locally, or add equivalent Streamlit Cloud secrets:

```toml
ACCOUNT_LOGIN_ONLY = true
DATABASE_URL = "postgresql://..."
ACCOUNT_SECRET = "a stable random secret of at least 32 UTF-8 bytes"
```

Requirements for this mode:

- Apply account storage migrations `001` and `002` before starting the app.
- Use a stable `ACCOUNT_SECRET` across restarts and deployed instances.
- Pre-provision demo users in the configured database.
- Never commit `secrets.toml`, database URLs, passwords, session handles, or account secrets.

Registration is disabled in the login-only entry point. A fresh Streamlit page session can require sign-in again, while saved conversations remain in PostgreSQL.

For the deployed recording demo, provision `sweaterco_demo_guest` in that PostgreSQL database and set `DEMO_LOGIN_PASSWORD` in the app's top-level Streamlit Secrets to the account's password. The sign-in form then prefills that demo account and still requires a click on **Sign in**. Its chat history is shared by everyone using the guest account. Remove the secret to return to blank sign-in fields. This deployment path is separate from the disposable `demo.cmd` accounts above; `start.cmd` remains anonymous without account Secrets.

Where registration is enabled, passwords must contain 6–15 characters and cannot match the username or use a blocked common password.

## User Interface

The app uses a chat-first, three-surface layout:

- **Sidebar:** navigation, recent chats, account controls, language, and appearance.
- **Conversation workspace:** chat history, Quick Workflows, and the centered composer.
- **Analysis panel:** metrics, evidence, warnings, assumptions, business context, and technical trace.

Primary navigation pages:

- AI Co-Pilot
- Morning Briefing
- Watches & Alerts
- Actions

Voice transcription fills the ordinary composer as an editable draft. Recording does not write to chat storage or execute business tools. The user must review and send the text through the normal submission path.

## Proactive Actions

The proactive layer separates read operations from mutations.

Supported briefing operations:

- Create a schedule.
- Update schedule fields.
- Pause or resume a schedule.
- Cancel a schedule.
- View schedules, next run times, and saved history.

Supported watch operations:

- Create an order watch.
- Cancel a watch.
- View active watches, checks, and alerts.

Mutating actions use the following journey:

```text
Request -> validated proposal -> user review -> confirm or reject -> execution -> audit history
```

Repeated request IDs reuse completion receipts and do not intentionally execute the same business mutation twice. Ambiguous or uncertain outcomes are surfaced for reconciliation instead of being blindly retried.

The standalone runners perform one due-task pass and exit:

```powershell
.\.venv\Scripts\python.exe -m app.agent.proactive.briefing.runner --database runtime_data/proactive.sqlite3
.\.venv\Scripts\python.exe -m app.agent.proactive.watches.runner --database runtime_data/proactive.sqlite3 --workspace local-demo
```

An external trusted scheduler is still required to invoke these runners when the browser is closed.

## Architecture

```text
app/ui/
  Streamlit layout, chat, accounts, voice drafts, evidence, and proactive pages

app/agent/
  LLM adapters, intent parsing, multi-turn planning, orchestration,
  answer polishing, speech, and proactive services

app/tools/
  Deterministic retrieval, order judgement, production analysis,
  risk discovery, morning briefing, and feasibility calculation

app/accounts/
  Identity, sessions, authorization, PostgreSQL storage,
  persistent conversation state, and completion receipts

data/
  Simulated factory source data
```

Storage boundaries:

- Anonymous chat: Streamlit session state.
- Account chat: PostgreSQL.
- Local proactive demo: `runtime_data/proactive.sqlite3` by default.
- Secrets: local `.env` and ignored `.streamlit/secrets.toml`.

## Tests and Quality Checks

Install the locked development environment first, then run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Latest full local regression on 12 November 2026:

```text
1586 passed, 13 skipped in 312.64 seconds
```

The 13 skipped tests require dedicated PostgreSQL test configuration. A passing mock or local suite must not be presented as final deployed-database acceptance.

The current repository-wide Ruff run still reports three pre-existing import-order findings in test/review files. The environment checker and new startup scripts pass their scoped checks; repository-wide lint cleanup remains part of Sprint 3 hardening.

## Evaluation

The checked-in live NLU evaluation contains 48 English, Chinese, mixed-language, colloquial, dual-intent, missing-slot, and unknown-intent cases.

Latest recorded result:

```text
48/48 exact matches (100.0%)
```

This result measures intent sequence, language, slots, missing slots, and issue types for one recorded model run. It does not measure complete A1/A2 execution, final answer quality, voice accuracy, account persistence, or proactive scheduling. A broader end-to-end ground-truth evaluation and failure analysis are still required for Sprint 3 reporting.

See:

- `evaluation/nlu_cases.json`
- `evaluation/nlu_evaluation_report.md`
- `docs/enhancement/NLU_EVALUATION_REPORT.md`

## Known Limitations

- The factory data is a static 1 April 2026 snapshot, not live telemetry.
- Natural-language chat and speech depend on configured OpenAI services.
- Live microphone accuracy has not been established by the mocked automated speech tests.
- Anonymous history is session-local.
- Final deployed login, logout, restart recovery, cookie metadata, and two-account isolation still require deployment acceptance testing.
- Local proactive persistence uses SQLite and is not suitable for ephemeral multi-instance cloud storage.
- No external scheduler is deployed by this repository.
- Watches support create and cancel, but not update, pause, or resume.
- Alerts are visible in the app; email, push, and voice delivery are not implemented.
- The current planner accepts at most two tasks in one natural-language request.
- Large evidence sets are downloadable, but response-level and full-audit evidence are not yet separate first-class contracts.
- Token usage, model cost, latency, and operational success metrics are not yet persisted.
- The current NLU report is not a full end-to-end product evaluation.

## Repository Structure

```text
.
|-- app/
|   |-- accounts/
|   |-- agent/
|   |-- tools/
|   `-- ui/
|-- data/
|-- docs/
|-- evaluation/
|-- notebooks/
|-- tests/
|-- tools/
|-- requirements.txt
|-- requirements-dev.txt
|-- requirements-lock.txt
|-- setup.cmd / setup.ps1
`-- start.cmd / start.ps1
```
