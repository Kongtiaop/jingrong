from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph

from app.agents.graph import SupervisorGraph
from app.schemas.agents import AgentState


def build_p0_graph():
    graph = SupervisorGraph()
    return graph.build()
