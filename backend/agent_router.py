from fastapi import APIRouter
from pydantic import BaseModel

try:
    from .agent_service import (
        execute_agent,
        DEFAULT_GUIDED_TOPICS
    )
except ImportError:
    from agent_service import (
        execute_agent,
        DEFAULT_GUIDED_TOPICS
    )


router = APIRouter()


class AgentSearchRequest(
    BaseModel
):
    question: str
    language: str = "English"


@router.get(
    "/api/agent-guide"
)
def agent_guide():
    return {
        "title":
            "What would you like to explore?",
        "message":
            "Choose a topic or ask your own question.",
        "topics":
            DEFAULT_GUIDED_TOPICS
    }


@router.post(
    "/api/agent-search"
)
def agent_search(
    request: AgentSearchRequest
):
    return execute_agent(
        question=request.question,
        language=request.language
    )
