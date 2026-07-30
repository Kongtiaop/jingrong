from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.schemas.agents import AgentState


class SupervisorGraph:
    """P0 LangGraph wiring placeholder.

    P0 only validates that the state graph can be constructed. Domain Agent
    nodes and tools are added from P1/P2/P3.
    """

    def __init__(self, max_iterations: int = 4, max_tool_calls: int = 8) -> None:
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls

    async def supervisor_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        return {"iteration_count": state.iteration_count + 1}

    def build(self):
        graph = StateGraph(AgentState)
        graph.add_node("supervisor", self.supervisor_node)
        graph.add_edge(START, "supervisor")
        graph.add_edge("supervisor", END)
        return graph.compile()
