"""Natural-language entry point that composes parsing and orchestration."""

from app.agent.contracts import AgentReply
from app.agent.intent_parser import IntentParser
from app.agent.llm_client import create_llm_client
from app.agent.orchestrator import AgentOrchestrator
from app.agent.settings import AgentSettings, LLMSettings


class AgentService:
    """Handle one English or Chinese user message end to end."""

    def __init__(self, parser: IntentParser, orchestrator: AgentOrchestrator) -> None:
        self.parser = parser
        self.orchestrator = orchestrator

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
        )

    def handle_text(self, user_text: str) -> AgentReply:
        """Parse natural language, run deterministic tools, and return a reply."""

        decision = self.parser.parse(user_text)
        reply = self.orchestrator.handle(decision)
        reply.metadata.setdefault("language", decision.language.value)
        return reply
