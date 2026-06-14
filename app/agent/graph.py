"""LangGraph state machine for HITL-gated issue creation.

The graph stops (interrupts) BEFORE the create_issue node. A human approves
or rejects via Slack; the slack_actions router calls resume_workflow() which
injects approval_granted via aupdate_state and resumes from the checkpoint.

Checkpointer is initialized once at startup so all callers (start_workflow,
resume_workflow) share the same compiled graph instance.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agent.nodes import classify, create_issue, draft, notify_slack, post_result
from app.agent.state import IssueProposalState

graph = None  # set by init_graph()


def _route_after_classify(state: IssueProposalState) -> str:
    return "draft" if state.get("classification") == "issue_worthy" else END


def _route_after_create(state: IssueProposalState) -> str:
    """post_result always runs so the human sees the outcome in Slack — even on rejection."""
    return "post_result"


def build_graph(checkpointer):
    builder = StateGraph(IssueProposalState)
    builder.add_node("classify", classify)
    builder.add_node("draft", draft)
    builder.add_node("notify_slack", notify_slack)
    builder.add_node("create_issue", create_issue)
    builder.add_node("post_result", post_result)

    builder.set_entry_point("classify")
    builder.add_conditional_edges("classify", _route_after_classify)
    builder.add_edge("draft", "notify_slack")
    builder.add_edge("notify_slack", "create_issue")
    builder.add_conditional_edges("create_issue", _route_after_create)
    builder.add_edge("post_result", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["create_issue"],
    )


def init_graph(checkpointer) -> None:
    global graph
    graph = build_graph(checkpointer)


async def start_workflow(initial_state: IssueProposalState) -> str:
    """Invoke the graph; returns the run_id once the interrupt is reached.

    Caller is responsible for generating run_id (already on initial_state).
    """
    if graph is None:
        raise RuntimeError("Graph not initialized. Call init_graph() first.")
    config = {"configurable": {"thread_id": initial_state["run_id"]}}
    await graph.ainvoke(initial_state, config=config)
    return initial_state["run_id"]


async def resume_workflow(
    run_id: str, *, approved: bool, reviewer_id: str
) -> IssueProposalState:
    """Resume from interrupt with the human's decision.

    If approved=True → create_issue runs the GitHub call.
    If approved=False → create_issue is a no-op but post_result still posts.
    """
    if graph is None:
        raise RuntimeError("Graph not initialized. Call init_graph() first.")
    config = {"configurable": {"thread_id": run_id}}
    await graph.aupdate_state(
        config,
        {
            "approval_granted": approved,
            "approved_by": reviewer_id,
        },
    )
    return await graph.ainvoke(None, config=config)


async def get_current_state(run_id: str) -> dict | None:
    if graph is None:
        return None
    config = {"configurable": {"thread_id": run_id}}
    snapshot = await graph.aget_state(config)
    if snapshot is None or snapshot.values is None:
        return None
    return snapshot.values
