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
    ) -> None:
        self.parser = parser
        self.orchestrator = orchestrator
        self.answer_polisher = answer_polisher
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
    ) -> "AgentService":
        """Build an API-backed service from local environment configuration."""

        llm_settings = LLMSettings.from_env(load_dotenv_file=load_dotenv_file)
        client = create_llm_client(llm_settings)
        return cls(
            parser=IntentParser(client),
            orchestrator=AgentOrchestrator(settings=agent_settings),
            answer_polisher=(
                AnswerPolisher(client, model_name=llm_settings.model)
                if llm_settings.provider == "openai"
                else None
            ),
        )

    def _finalize(self, reply: AgentReply) -> AgentReply:
        if self.answer_polisher is None:
            return reply
        return self.answer_polisher.polish(reply)

    def handle_text(self, user_text: str) -> AgentReply:
        """Parse natural language, run deterministic tools, and return a reply."""

        decision = self.parser.parse(user_text)
        reply = self.orchestrator.handle(decision)
        reply.metadata.setdefault("language", decision.language.value)
        return self._finalize(reply)

    def handle_turn(self, user_text: str, session: ChatSession | None = None) -> TurnResult:
        """Handle contextual chat. The caller stores the returned per-thread session."""

        result = self.turn_service.handle_turn(user_text, session)
        return TurnResult(self._finalize(result.reply), result.session)
