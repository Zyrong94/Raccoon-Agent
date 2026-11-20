"""Agent execution and session facts."""
__all__ = ["AgentRun", "AgentSession", "run_agent"]

def __getattr__(name):
    if name in {"AgentRun", "run_agent"}:
        from raccoon.modules.agent.run import AgentRun, run_agent
        return AgentRun if name == "AgentRun" else run_agent
    if name == "AgentSession":
        from raccoon.modules.agent.session import AgentSession
        return AgentSession
    raise AttributeError(name)
