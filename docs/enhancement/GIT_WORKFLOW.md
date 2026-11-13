# SweaterCo — Git Collaboration Workflow

**Version:** 0.1  
**Shared integration branch:** `integration/intent-enhancement`  
**Initial base commit:** `0aff410`

## 1. Branch Structure

The natural language enhancement project uses one shared integration branch and five individual feature branches.

| Developer | Feature Branch | Responsibility |
|---|---|---|
| Person 1 | `feat/nlu-understanding` | Intent understanding |
| Person 2 | `feat/nlu-evaluation` | Evaluation and testing |
| Person 3 | `feat/conversation-memory` | Conversation state |
| Person 4 | `feat/conversation-clarification` | Clarification |
| Person 5 | `feat/multi-intent-planning` | Task planning |

All feature branches must be created from the same approved integration baseline.

## 2. Protected Branches

Do not directly modify or push enhancement code to:

- `main`
- `feat/retrieval-tools`
- `feat/a2-judgement-tools`
- `feat/b-agent-orchestration`

The shared integration branch should receive changes through reviewed Pull Requests.

## 3. Creating a Feature Branch

After the integration branch has been pushed to GitHub:

```bash
git fetch origin
git switch -c feat/nlu-understanding origin/integration/intent-enhancement
git push -u origin feat/nlu-understanding
```

Replace `feat/nlu-understanding` with your assigned branch name.

If the branch already exists locally, switch to it rather than creating it again.

## 4. Daily Development Workflow

Before working:

```bash
git switch YOUR_FEATURE_BRANCH
git status
git pull --ff-only
```

After modifying files:

```bash
git status
git add path/to/your/files
git commit -m "feat: describe your change"
git push
```

Use explicit file paths with `git add`. Avoid staging unrelated local files.

## 5. Pull Request Workflow

1. Push the feature branch.
2. Open a Pull Request on GitHub.
3. Set the base branch to `integration/intent-enhancement`.
4. Describe the changes, tests, and interface impact.
5. Request review from at least one other team member.
6. Run all existing and new automated tests.
7. Merge only after review and successful checks.

Do not merge unfinished modules simply to share work.

## 6. Shared Interface Changes

The following files are shared or protected:

- `app/agent/enhancement/contracts.py`
- `docs/enhancement/INTERFACE_CONTRACT.md`
- `app/agent/service.py`
- `app/agent/contracts.py`
- `app/agent/intents.py`
- `app/agent/orchestrator.py`

If a developer needs to modify a shared interface:

1. Describe the proposed change in a GitHub Issue or PR.
2. Discuss the impact with affected groups.
3. Obtain agreement before implementation.
4. Assign one developer to make the shared change.
5. Update tests and interface documentation together.

## 7. UI and API Isolation

UI/API development should remain on separate feature branches.

Suggested branches:

- `feat/streamlit-agent-ui`
- `feat/api-deployment`

UI/API developers should continue using the existing `AgentService.handle_text()` interface until the enhanced interface is approved and integrated.

Do not commit API keys, `.env`, or local credentials.

## 8. Synchronizing with Integration

When the shared integration branch has new accepted changes, developers may incorporate them into their feature branches.

First check for uncommitted changes:

```bash
git status
```

Then:

```bash
git fetch origin
git switch YOUR_FEATURE_BRANCH
git merge origin/integration/intent-enhancement
```

Resolve any conflicts carefully and rerun tests before pushing.

Do not use destructive reset commands to resolve conflicts.

## 9. Testing Requirements

Before opening or merging a PR:

```bash
python -m pytest -q
python -m ruff check .
git diff --check
```

New functionality should include automated tests.

The original 85 tests must continue to pass.

## 10. Local Files and Security

Do not commit:

- `.env`
- API keys
- `.venv/`
- `__pycache__/`
- Local Streamlit development skills under `.agents/` or `.claude/`

Use explicit staging commands rather than `git add .`.

## 11. Final Integration

Once enhancement modules pass review:

1. Integrate the modules through a backward-compatible AgentService entry point.
2. Run complete regression and integration tests.
3. Coordinate with UI/API developers.
4. Test real user conversations.
5. Merge to the approved final branch only after team agreement.