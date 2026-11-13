# SweaterCo — Natural Language Enhancement Development Plan

**Version:** Draft v0.1  
**Development branch:** `integration/intent-enhancement`  
**Base commit:** `0aff410`  
**Team size:** 5 members

## 1. Project Objective

Enhance the existing SweaterCo Assistant to better understand natural language, maintain conversation context, clarify incomplete requests, and handle limited multi-intent tasks.

The project will reuse the existing A1 Retrieval, A2 Judgement, and B Agent Orchestration components.

No model fine-tuning is required for the initial implementation.

## 2. Team Structure

### Group 1 — Natural Language Understanding (2 members)

**Scope:** Intent recognition, slot extraction, language robustness, and evaluation.

**Member 1: Understanding Development**

Responsibilities:
- Improve recognition of informal, ambiguous, and bilingual user requests.
- Enhance intent classification and slot extraction.
- Develop prompt improvements and representative examples.
- Maintain the understanding module.

Primary directory:

`app/agent/enhancement/understanding/`

**Member 2: Evaluation Development**

Responsibilities:
- Build a natural language evaluation dataset.
- Implement automated intent and slot accuracy evaluation.
- Identify common errors and support iterative improvements.
- Maintain evaluation tests and reports.

Primary directory:

`tests/enhancement/understanding/`

**Group deliverables:**
- Improved natural language understanding module.
- Evaluation dataset with approximately 40–60 cases.
- Automated evaluation scripts.
- Evaluation report.

### Group 2 — Conversation & Clarification (2 members)

**Scope:** Multi-turn interactions, context memory, slot filling, and clarification.

**Member 3: Conversation Development**

Responsibilities:
- Implement ConversationState.
- Preserve relevant intent and slot information across turns.
- Support parameter updates and task switching.
- Develop multi-turn tests.

Primary files:

`app/agent/enhancement/conversation/memory.py`

`tests/enhancement/conversation/test_memory.py`

**Member 4: Clarification Development**

Responsibilities:
- Detect incomplete or ambiguous requests.
- Implement clarification strategies.
- Reuse existing required-slot validation.
- Prevent execution when essential parameters are missing.

Primary files:

`app/agent/enhancement/conversation/clarification.py`

`tests/enhancement/conversation/test_clarification.py`

**Group deliverables:**
- Conversation state management.
- Multi-turn slot filling.
- Clarification handling.
- Automated conversation tests.

### Group 3 — Planning & Response Integration (1 member)

**Scope:** Limited multi-intent planning and grounded result aggregation.

**Member 5: Planning Development**

Responsibilities:
- Identify and represent multiple business tasks.
- Support up to two tasks per user request.
- Reuse existing orchestration and business tools.
- Preserve evidence, warnings, and assumptions when combining results.
- Develop planning tests.

Primary files:

`app/agent/enhancement/planning/planner.py`

`app/agent/enhancement/planning/response.py`

`tests/enhancement/planning/test_planner.py`

**Group deliverables:**
- Multi-intent task planning module.
- Grounded result aggregation.
- Automated planning tests.

## 3. Shared Responsibilities

All members must:

1. Participate in shared interface design.
2. Develop and test their assigned functionality.
3. Maintain their own module documentation.
4. Review relevant pull requests.
5. Participate in integration and end-to-end testing.

No member should independently change shared interfaces without review.

## 4. File Ownership

| Area | Owner |
|---|---|
| `enhancement/understanding/` | Group 1 |
| `enhancement/conversation/` | Group 2 |
| `enhancement/planning/` | Group 3 |
| `tests/enhancement/understanding/` | Group 1 |
| `tests/enhancement/conversation/` | Group 2 |
| `tests/enhancement/planning/` | Group 3 |
| `enhancement/contracts.py` | Designated integration maintainer |
| `docs/enhancement/INTERFACE_CONTRACT.md` | Designated integration maintainer |

Shared files require team review before modification.

## 5. Protected Existing Components

During independent module development, do not modify:

- `app/agent/service.py`
- `app/agent/contracts.py`
- `app/agent/intents.py`
- `app/agent/orchestrator.py`
- `app/agent/llm_client.py`
- `app/agent/settings.py`
- `app/tools/`
- `app/ui/`

Any required changes must be proposed and handled during integration.

This prevents interference with ongoing UI and API development.

## 6. Git Branch Strategy

Shared integration branch:

`integration/intent-enhancement`

Proposed feature branches:

- Group 1 Development: `feat/nlu-understanding`
- Group 1 Evaluation: `feat/nlu-evaluation`
- Group 2 Memory: `feat/conversation-memory`
- Group 2 Clarification: `feat/conversation-clarification`
- Group 3 Planning: `feat/multi-intent-planning`

Each member develops on their own feature branch.

Pull requests target `integration/intent-enhancement`.

Do not directly push development changes to `main` or the existing B branch.

## 7. Development Phases

**Phase 0 — Shared Preparation**
- Confirm interfaces.
- Establish directory ownership.
- Create common integration baseline.

**Phase 1 — Independent Development**
- Implement individual modules.
- Add automated tests.
- Maintain module README files.

**Phase 2 — Module Integration**
- Review pull requests.
- Connect enhancement modules.
- Preserve existing AgentService behavior.
- Run regression tests.

**Phase 3 — UI/API Integration**
- Coordinate with the UI/API developers.
- Expose a backward-compatible multi-turn entry point.
- Verify end-to-end interaction.

**Phase 4 — Evaluation & Demonstration**
- Test natural language variations.
- Test multi-turn clarification.
- Test limited multi-intent requests.
- Record limitations and evaluation results.

## 8. Acceptance Criteria

- Existing 85 tests continue to pass.
- New modules have automated tests.
- Existing business rules remain unchanged.
- Incomplete requests do not trigger unsupported conclusions.
- Conversation state remains isolated between sessions.
- Evidence and warnings remain traceable.
- Existing UI/API work is not overwritten.
- New features can be demonstrated through the integrated Assistant.

## 9. Initial Scope Limitations

The first enhancement version will not include:

- Model fine-tuning.
- Arbitrary SQL generation.
- Unlimited multi-agent planning.
- New A1/A2 business calculations.
- Unrestricted multi-intent execution.
- Major UI redesign.

The priority is reliable intent understanding and controlled task execution.