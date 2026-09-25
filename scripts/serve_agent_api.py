#!/usr/bin/env python3
"""
FastAPI service for PydanticAI interaction with the MCP server catalog.

Usage:
    python scripts/serve_agent_api.py
    python scripts/serve_agent_api.py --host 0.0.0.0 --port 8000

Endpoints:
    POST   /sessions              Create a new chat session
    POST   /sessions/{id}/chat    Send a message and get agent response
    GET    /sessions/{id}         Get session history and metadata
    DELETE /sessions/{id}         Close a session and release MCP connections
    GET    /models                List available registered models
    GET    /servers               List available MCP servers
"""
import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from dataclasses import asdict
from typing import Any, Dict, List, Optional

# Load workspace configuration independently of the process working directory.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
WORKSPACE_DIR = PROJECT_DIR
sys.path.insert(0, str(PROJECT_DIR))
try:
    from dotenv import load_dotenv
    load_dotenv(WORKSPACE_DIR / ".env", override=False)
    load_dotenv(WORKSPACE_DIR / ".env.mcp", override=False)
except ImportError:
    pass
os.environ.setdefault("UV_CACHE_DIR", str(WORKSPACE_DIR / ".uv-cache"))
# MCP server commands use paths relative to the project root.
os.chdir(PROJECT_DIR)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from uvicorn import run as uvicorn_run

from agent_env.execution_limits import ExecutionLimits
from agent_env.pydantic_agent import PydanticAIAgentAdapter
from agent_env.server_catalog import load_server_commands
from llm.factory import LLMFactory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory session store
_sessions: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ExecutionLimitsRequest(BaseModel):
    max_rounds: Optional[int] = Field(
        None,
        ge=1,
        le=50,
        description="Optional planning-round cap; null allows natural termination.",
    )
    max_tool_calls: Optional[int] = Field(
        None,
        ge=1,
        le=200,
        description="Optional tool-call cap; null allows natural tool use.",
    )
    max_total_tokens: Optional[int] = Field(
        None,
        ge=1000,
        le=1000000,
        description="Optional token cap; null leaves agent tokens uncapped.",
    )


class CreateSessionRequest(BaseModel):
    model: Optional[str] = Field(
        None, description="Registered model name. Required unless using direct endpoint."
    )
    base_url: Optional[str] = Field(None, description="Direct OpenAI-compatible base URL")
    api_key: Optional[str] = Field("", description="API key for direct endpoint")
    model_name: Optional[str] = Field(None, description="Actual model name for direct endpoint")
    servers: List[str] = Field(default_factory=list, description="List of MCP server names (empty = all)")
    tool_allowlists: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Optional per-server tool names exposed to the agent.",
    )
    shared_mcp_urls: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional reusable MCP HTTP endpoints keyed by server name.",
    )
    execution_limits: ExecutionLimitsRequest = Field(
        default_factory=ExecutionLimitsRequest,
        description="Per-turn execution budgets.",
    )


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message")
    timeout: int = Field(300, ge=30, le=600, description="Timeout in seconds")


class ChatResponse(BaseModel):
    session_id: str
    response: str
    total_rounds: int = 0
    tool_calls: int = 0
    total_tokens: int = 0
    termination_reason: Optional[str] = None
    filtered_tool_calls: int = 0
    error: Optional[str] = None


class SessionInfoResponse(BaseModel):
    session_id: str
    model_name: str
    servers: List[str]
    created_at: float
    updated_at: float
    history: List[Dict]


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API server starting")
    yield
    logger.info("API server shutting down, closing %d session(s)", len(_sessions))
    for session in list(_sessions.values()):
        try:
            await session.close()
        except Exception as e:
            logger.warning("Error closing session %s: %s", session.session_id, e)
    _sessions.clear()


app = FastAPI(
    title="PrivacyTrace Agent API",
    description="REST API for the PydanticAI-backed PrivacyTrace agent.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_session(session_id: str) -> Any:
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return session


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {
        "service": "PrivacyTrace Agent API",
        "docs": "/docs",
        "endpoints": {
            "models": "/models",
            "servers": "/servers",
            "create_session": "POST /sessions",
            "active_sessions": "GET /sessions",
            "chat": "POST /sessions/{id}/chat",
            "get_session": "GET /sessions/{id}",
            "connections": "GET /sessions/{id}/connections",
            "close_session": "DELETE /sessions/{id}",
        },
    }


@app.get("/models")
async def list_models():
    configs = LLMFactory.get_model_configs()
    return {
        "models": [
            {
                "name": name,
                "provider_type": cfg.provider_type,
                "model_name": cfg.config.get("model_name", cfg.config.get("deployment_name")),
            }
            for name, cfg in configs.items()
        ]
    }


@app.get("/servers")
async def list_servers():
    commands = load_server_commands(PROJECT_DIR / "mcp_servers/commands.json")
    return {"servers": sorted(commands)}


@app.get("/sessions")
async def list_active_sessions():
    return {
        "sessions": [
            session.connection_status()
            for session in _sessions.values()
        ]
    }


@app.post("/sessions", response_model=SessionInfoResponse, status_code=201)
async def create_session(req: CreateSessionRequest):
    try:
        limit_values = (
            req.execution_limits.model_dump()
            if hasattr(req.execution_limits, "model_dump")
            else req.execution_limits.dict()
        )
        execution_limits = ExecutionLimits.from_mapping(limit_values)

        commands = load_server_commands(PROJECT_DIR / "mcp_servers/commands.json")
        servers = req.servers or list(commands)
        unknown_servers = set(servers) - set(commands)
        if unknown_servers:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown MCP servers: {sorted(unknown_servers)}",
            )

        base_url = req.base_url
        api_key = req.api_key or ""
        model_name = req.model_name
        if not base_url:
            configs = LLMFactory.get_model_configs()
            if not configs:
                raise HTTPException(
                    status_code=400,
                    detail="No registered models available",
                )
            selected_name = req.model or next(iter(configs))
            if selected_name not in configs:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown registered model: {selected_name}",
                )
            model_config = configs[selected_name]
            if model_config.provider_type == "azure":
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "PydanticAI requires an OpenAI-compatible base_url "
                        "for Azure models."
                    ),
                )
            base_url = model_config.config.get("base_url")
            api_key = str(model_config.config.get("api_key", ""))
            model_name = model_config.config.get("model_name")
        if not base_url or not model_name:
            raise HTTPException(
                status_code=422,
                detail="base_url and model_name are required",
            )
        session = PydanticAIAgentAdapter(
            base_url=str(base_url),
            api_key=api_key,
            model_name=str(model_name),
            server_names=servers,
            execution_limits=execution_limits,
            tool_allowlists=req.tool_allowlists,
            shared_mcp_urls=req.shared_mcp_urls,
            project_dir=PROJECT_DIR,
        )

        await session.initialize()
        _sessions[session.session_id] = session

        return SessionInfoResponse(
            session_id=session.session_id,
            model_name=session.model_name,
            servers=session.server_names,
            created_at=session.created_at,
            updated_at=session.updated_at,
            history=session.history,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create session")
        raise HTTPException(status_code=500, detail=f"Failed to create session: {e}")


@app.post("/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(session_id: str, req: ChatRequest):
    session = _require_session(session_id)
    try:
        turn = await session.respond(
            req.message,
            timeout_seconds=req.timeout,
        )
        result = asdict(turn)
        if "error" in result:
            # Still return a 200 with the error in the response field
            logger.warning("Chat turn produced error: %s", result.get("error"))
        return ChatResponse(
            session_id=session_id,
            response=result.get("response", ""),
            total_rounds=result.get("total_rounds", 0),
            tool_calls=result.get("tool_calls", 0),
            total_tokens=result.get("total_tokens", 0),
            termination_reason=result.get("termination_reason"),
            filtered_tool_calls=result.get("filtered_tool_calls", 0),
            error=result.get("error"),
        )
    except Exception as e:
        logger.exception("Chat failed")
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}")


@app.get("/sessions/{session_id}", response_model=SessionInfoResponse)
async def get_session(session_id: str):
    session = _require_session(session_id)
    return SessionInfoResponse(
        session_id=session.session_id,
        model_name=session.model_name,
        servers=session.server_names,
        created_at=session.created_at,
        updated_at=session.updated_at,
        history=session.history,
    )


@app.get("/sessions/{session_id}/connections")
async def get_session_connections(session_id: str):
    return _require_session(session_id).connection_status()


@app.delete("/sessions/{session_id}")
async def close_session(session_id: str):
    session = _sessions.pop(session_id, None)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    await session.close()
    return {"status": "closed", "session_id": session_id}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="FastAPI server for the PrivacyTrace PydanticAI agent"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))
    uvicorn_run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
