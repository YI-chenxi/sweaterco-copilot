"""Final-answer polish tests use offline structured mock responses only."""

from app.agent.answer_polisher import AnswerPolisher
from app.agent.contracts import AgentReply, Evidence
from app.agent.intent_parser import IntentParser
from app.agent.llm_client import LLMClientError, MockLLMClient
from app.agent.orchestrator import AgentOrchestrator
from app.agent.service import AgentService
from tests.test_chat_integration import payload, service, task


def draft(**overrides):
    values = {
        "text": (
            "**Order ORD-057 does not currently show a delivery risk.**\n\n"
            "The due date passed 58 days ago."
        ),
        "evidence": [Evidence("orders.csv", "ORD-057", "source row")],
        "warnings": ["source warning"],
        "metadata": {"language": "en", "intent": "order_status"},
    }
    values.update(overrides)
    return AgentReply(**values)


def test_polisher_changes_only_public_text_and_preserves_structured_fields():
    client = MockLLMClient(
        [
            {
                "answer": (
                    "**ORD-057 does not currently show a delivery risk.**\n\n"
                    "Its due date passed 58 days ago."
                )
            }
        ]
    )
    original = draft()

    reply = AnswerPolisher(client, model_name="gpt-6-luna").polish(original)

    assert reply.text.startswith("**ORD-057")
    assert reply.evidence == original.evidence
    assert reply.warnings == original.warnings
    assert reply.metadata["response_polished"] is True
    assert reply.metadata["response_polisher_model"] == "gpt-6-luna"
    assert original.metadata.get("response_polished") is None
    assert client.calls[0]["schema"]["additionalProperties"] is False
    assert "source row" not in client.calls[0]["user_prompt"]


def test_polisher_rejects_changed_numbers_and_uses_deterministic_fallback():
    client = MockLLMClient(
        [
            {
                "answer": (
                    "**ORD-057 does not currently show a delivery risk.**\n\n"
                    "Its due date passed 59 days ago."
                )
            }
        ]
    )
    original = draft()

    reply = AnswerPolisher(client, model_name="gpt-6-luna").polish(original)

    assert reply.text == original.text
    assert reply.metadata["response_polished"] is False
    assert reply.metadata["response_polish_fallback"] is True


def test_polisher_rejects_internal_terms_or_model_failure():
    for response in (
        {"answer": "**Task task-001 is READY.**\n\nThe planner completed it."},
        {
            "answer": (
                "**Order ORD-057 does not currently show a delivery risk.**\n\n"
                "**Extra label:** The due date passed 58 days ago."
            )
        },
        LLMClientError("offline"),
    ):
        reply = AnswerPolisher(MockLLMClient([response]), model_name="gpt-6-luna").polish(draft())
        assert reply.text == draft().text
        assert reply.metadata["response_polish_fallback"] is True


def test_clarification_polish_must_keep_a_direct_question():
    original = draft(
        text=(
            "**I need one more detail before I can continue.**\n\n"
            "What delivery date should I use for the 800-piece TOPS order?"
        ),
        needs_clarification=True,
        suggested_question="What delivery date should I use?",
    )
    client = MockLLMClient(
        [{"answer": "**I still need the delivery date for the 800-piece TOPS order.**"}]
    )

    reply = AnswerPolisher(client, model_name="gpt-6-luna").polish(original)

    assert reply.text == original.text
    assert reply.suggested_question == original.suggested_question


def test_agent_service_applies_polisher_after_deterministic_execution():
    parser_client = MockLLMClient(
        [
            {
                "intent": "order_status",
                "language": "en",
                "order_id": "ORD-057",
                "stage": None,
                "category": None,
                "pieces": None,
                "requested_delivery_date": None,
                "top_n": None,
                "missing_slots": [],
            }
        ]
    )
    polish_client = MockLLMClient(
        [
            {
                "answer": (
                    "**ORD-057 does not currently show a delivery risk.**\n\n"
                    "The due date passed 58 days ago, and the last recorded activity "
                    "was 66 days ago."
                )
            }
        ]
    )
    service = AgentService(
        IntentParser(parser_client),
        AgentOrchestrator(),
        AnswerPolisher(polish_client, model_name="gpt-6-luna"),
    )

    reply = service.handle_text("How is ORD-057?")

    assert reply.metadata["response_polished"] is True
    assert reply.text.startswith("**ORD-057")
    assert reply.metadata["structured_result"]["order_id"] == "ORD-057"


def test_contextual_turn_polishes_reply_without_changing_saved_session():
    agent, _, _ = service(payload(task(order_id="ORD-057")))
    polish_client = MockLLMClient(
        [
            {
                "answer": (
                    "**ORD-057 does not currently show a delivery risk.**\n\n"
                    "The due date passed 58 days ago, and its last recorded activity "
                    "was 66 days ago."
                )
            }
        ]
    )
    agent.answer_polisher = AnswerPolisher(polish_client, model_name="gpt-6-luna")

    result = agent.handle_turn("How is ORD-057?")

    assert result.reply.metadata["response_polished"] is True
    assert result.session.state.tasks[0].status.value == "completed"
    assert result.session.state.tasks[0].decision.slots["order_id"] == "ORD-057"
