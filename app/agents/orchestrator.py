from __future__ import annotations
from app.ports.agent import Agent, AgentContext
from app.schemas.agents import AgentResult

class AgentOrchestrator:
    def __init__(self, agents: list[Agent] | None = None):
        self._agents = {agent.name: agent for agent in (agents or [])}

    def register(self, agent: Agent) -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    async def run(self, name: str, message: str, context: AgentContext) -> AgentResult:
        agent = self.get(name)
        if agent is None:
            return AgentResult(agent_name=name, status="error", summary="agent not registered", errors=[f"unknown agent: {name}"])
        return await agent.run(message, context)
