import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from engine.ai_provider import SUPPORTED_PROVIDERS, test_ai_connection
from engine.ai_session import execute_chat_turn, session_manager
from engine.state import state

logger = logging.getLogger("CriptoBotEngine")

router = APIRouter(prefix="/api/ai", tags=["ai"])


def verify_ai_pin(request: Request, x_auth_pin: str = Header(None)):
    if not state.auth_pin:
        return
    if not x_auth_pin or not secrets.compare_digest(str(x_auth_pin).strip(), str(state.auth_pin).strip()):
        client_host = request.client.host if request.client else "unknown"
        logger.warning(f"Unauthorized AI API access attempt blocked from {client_host}")
        raise HTTPException(status_code=401, detail="Invalid PIN")


class AiTestRequest(BaseModel):
    provider: str = "google"
    model: str = "gemini-3.1-flash"
    api_key: str | None = ""
    base_url: str | None = ""


class AiChatRequest(BaseModel):
    query: str
    session_id: str | None = "default"


@router.get("/providers")
async def get_ai_providers():
    """
    Returns metadata on all supported LangChain model providers.
    """
    return JSONResponse({
        "providers": SUPPORTED_PROVIDERS,
        "active_provider": getattr(state, "ai_provider", "google"),
        "active_model": getattr(state, "ai_model", "gemini-3.1-flash")
    })


@router.get("/models")
async def get_ai_models(provider: str = Query("google")):
    """
    Returns available/recommended models for the specified provider.
    If Google and key is configured, also attempts live model discovery.
    """
    prov = (provider or "google").strip().lower()
    info = SUPPORTED_PROVIDERS.get(prov, SUPPORTED_PROVIDERS["custom"])
    models = list(info.get("models", []))

    if prov == "google":
        key = getattr(state, "ai_api_key", "") or getattr(state, "gemini_api_key", "")
        if key:
            try:
                from engine.ai_analyst import fetch_available_gemini_models
                discovered = await fetch_available_gemini_models(key)
                if discovered:
                    # Merge discovered with preset models
                    for m in discovered:
                        if m not in models:
                            models.append(m)
            except Exception as e:
                logger.debug(f"Dynamic Google model discovery skipped: {e}")

    return JSONResponse({
        "provider": prov,
        "models": models,
        "default_model": info.get("default_model", "")
    })


@router.post("/test", dependencies=[Depends(verify_ai_pin)])
async def api_test_ai(req: AiTestRequest):
    """
    Tests connectivity, credentials, and response latency for any provider.
    """
    prov = req.provider.strip().lower()
    model = req.model.strip()
    key = (req.api_key or "").strip()
    base = (req.base_url or "").strip()

    # Fall back to state credentials if not explicitly passed in payload
    if not key:
        if prov == getattr(state, "ai_provider", "google"):
            key = getattr(state, "ai_api_key", "")
        elif prov == "google":
            key = getattr(state, "gemini_api_key", "")
        elif prov == "groq":
            key = getattr(state, "groq_api_key", "")

    if not base and prov == getattr(state, "ai_provider", ""):
        base = getattr(state, "ai_base_url", "")

    result = await test_ai_connection(
        provider=prov,
        model_name=model,
        api_key=key,
        base_url=base
    )
    return JSONResponse(result)


@router.post("/chat", dependencies=[Depends(verify_ai_pin)])
async def api_ai_chat(req: AiChatRequest):
    """
    Conversational assistant endpoint maintaining multi-turn memory.
    """
    sid = (req.session_id or "default").strip()
    market_ctx = state.to_dict()

    key = getattr(state, "ai_api_key", "") or getattr(state, "gemini_api_key", "")
    fb_key = getattr(state, "ai_fallback_api_key", "") or getattr(state, "groq_api_key", "")

    result = await execute_chat_turn(
        query=req.query,
        session_id=sid,
        market_context=market_ctx,
        provider=getattr(state, "ai_provider", "google"),
        model=getattr(state, "ai_model", "gemini-3.1-flash"),
        api_key=key,
        base_url=getattr(state, "ai_base_url", ""),
        fallback_provider=getattr(state, "ai_fallback_provider", "groq"),
        fallback_model=getattr(state, "ai_fallback_model", "llama-3.3-70b-versatile"),
        fallback_api_key=fb_key
    )
    return JSONResponse(result)


@router.get("/chat/history", dependencies=[Depends(verify_ai_pin)])
async def api_get_chat_history(session_id: str = Query("default")):
    """
    Retrieves the conversation message history for a session.
    """
    history = session_manager.get_history(session_id)
    return JSONResponse({
        "session_id": session_id,
        "history": history
    })


@router.delete("/chat/history", dependencies=[Depends(verify_ai_pin)])
async def api_clear_chat_history(session_id: str = Query("default")):
    """
    Clears the conversational session memory.
    """
    cleared = session_manager.clear_session(session_id)
    return JSONResponse({
        "session_id": session_id,
        "cleared": cleared
    })


@router.get("/sessions", dependencies=[Depends(verify_ai_pin)])
async def api_list_sessions():
    """
    Lists active conversational sessions and message statistics.
    """
    sessions = session_manager.list_active_sessions()
    return JSONResponse({"sessions": sessions})
