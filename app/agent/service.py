"""Natural-language entry point that composes parsing and orchestration."""

from app.agent.answer_polisher import AnswerPolisher
from app.agent.contracts import AgentReply
from app.agent.enhancement.understanding.parser import EnhancedIntentParser
from app.agent.intent_parser import IntentParser
from app.agent.llm_client import create_llm_client
from app.agent.orchestrator import AgentOrchestrator
from app.agent.settings import AgentSettings, LLMSettings
from app.agent.turn_service import ChatSession, ConversationTurnService, TurnResult


class AgentService:
    """Handle one English or Chinese user message end to end."""

    def __init__(
        self,
        parser: IntentParser,
        orchestrator: AgentOrchestrator,
        answer_polisher: AnswerPolisher | None = None,
        proactive_application=None,
    ) -> None:
        self.parser = parser
        self.orchestrator = orchestrator
        self.answer_polisher = answer_polisher
        self.proactive_application = proactive_application
        self.turn_service = ConversationTurnService(
            EnhancedIntentParser(
                parser.client,
                reference_date=orchestrator.settings.reference_date,
            ),
            orchestrator,
        )

    @classmethod
    def from_env(
        cls,
        *,
        agent_settings: AgentSettings | None = None,
        load_dotenv_file: bool = True,
        proactive_identity_provider=None,
        proactive_authorizer=None,
    ) -> "AgentService":
        """Build an API-backed service from local environment configuration."""

        llm_settings = LLMSettings.from_env(load_dotenv_file=load_dotenv_file)
        client = create_llm_client(llm_settings)
        proactive_application = None
        identity_provider = proactive_identity_provider
        authorizer = proactive_authorizer
        if (identity_provider is None) != (authorizer is None):
            raise ValueError(
                "proactive_identity_provider and proactive_authorizer must be supplied together"
            )
        if identity_provider is None and authorizer is None:
            import os

            if os.getenv("PROACTIVE_DEMO_MODE", "").casefold() == "true":
                workspace_id = os.getenv("PROACTIVE_DEMO_WORKSPACE", "")
                if workspace_id:
                    from types import SimpleNamespace

                    def demo_identity_provider():
                        return SimpleNamespace(user_id="demo-operator", workspace_id=workspace_id)

                    def demo_authorizer(actor, workspace, operation, proposal):
                        return actor == "demo-operator" and workspace == workspace_id

                    identity_provider = demo_identity_provider
                    authorizer = demo_authorizer
        if identity_provider is not None and authorizer is not None:
            from app.agent.proactive.integration import build_proactive_application

            proactive_application = build_proactive_application(
                llm_client=client,
                actor_provider=lambda: identity_provider().user_id,
                workspace_provider=lambda: identity_provider().workspace_id,
                authorizer=authorizer,
            )
        return cls(
            parser=IntentParser(client),
            orchestrator=AgentOrchestrator(settings=agent_settings),
            answer_polisher=(
                AnswerPolisher(client, model_name=llm_settings.model)
                if llm_settings.provider == "openai"
                else None
            ),
            proactive_application=proactive_application,
        )

    def _finalize(self, reply: AgentReply) -> AgentReply:
        if self.answer_polisher is None:
            return reply
        return self.answer_polisher.polish(reply)

    def handle_text(self, user_text: str) -> AgentReply:
        """Parse natural language, run deterministic tools, and return a reply."""

        proactive_reply = self._handle_proactive(user_text)
        if proactive_reply is not None:
            return self._finalize(proactive_reply)

        decision = self.parser.parse(user_text)
        reply = self.orchestrator.handle(decision)
        reply.metadata.setdefault("language", decision.language.value)
        return self._finalize(reply)

    def handle_turn(self, user_text: str, session: ChatSession | None = None) -> TurnResult:
        """Handle contextual chat. The caller stores the returned per-thread session."""

        proactive_reply = self._handle_proactive(user_text)
        if proactive_reply is not None:
            from copy import deepcopy

            return TurnResult(
                proactive_reply, deepcopy(session) if session is not None else ChatSession()
            )

        result = self.turn_service.handle_turn(user_text, session)
        return TurnResult(self._finalize(result.reply), result.session)

    def _handle_proactive(self, user_text: str) -> AgentReply | None:
        """Keep scheduled commands out of the ordinary immediate-briefing route."""
        if self.proactive_application is None:
            return None
        result = self.proactive_application.handle_text(user_text)
        if not result.get("handled"):
            return None
        status = result.get("status", "error")
        message = result.get("message") or result.get("data", {}).get("message")
        if not message:
            if status == "needs_clarification":
                message = self.proactive_application._clarification(result["command"])
            elif result.get("ok"):
                message = f"Proactive action status: {status}."
            else:
                message = (
                    f"Proactive action could not be completed ({result.get('error_code', status)})."
                )
        return AgentReply(
            text=message,
            needs_clarification=status == "needs_clarification",
            suggested_question=message if status == "needs_clarification" else None,
            metadata={
                "intent": "proactive",
                "language": result.get("command", {}).get("language", "en"),
                "proactive": result,
                "status": status,
            },
        )
