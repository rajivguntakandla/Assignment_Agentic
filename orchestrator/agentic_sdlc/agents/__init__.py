"""Stage agents. Each agent receives only the artifacts its stage declares in the DAG."""

from .base import AgentContext, AgentFailure, AgentResult
from .design import DesignAgent
from .documentation import DocumentationAgent
from .implementation import ImplementationAgent
from .planning import PlanningAgent
from .release import ReleaseAgent
from .requirements import RequirementsAgent
from .security import SecurityReviewAgent
from .verification import VerificationAgent

AGENTS = {
    "requirements": RequirementsAgent(),
    "planning": PlanningAgent(),
    "design": DesignAgent(),
    "implementation": ImplementationAgent(),
    "verification": VerificationAgent(),
    "security_review": SecurityReviewAgent(),
    "documentation": DocumentationAgent(),
    "release": ReleaseAgent(),
}

__all__ = ["AGENTS", "AgentContext", "AgentFailure", "AgentResult"]
