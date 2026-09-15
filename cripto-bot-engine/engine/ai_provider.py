import time
import json
import logging
from typing import Optional, Dict, Any, List, Union

from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger("CriptoBotEngine")

SUPPORTED_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "google": {
        "id": "google",
        "name": "Google Gemini",
        "default_model": "gemini-3.1-flash",
        "models": [
            "gemini-3.1-flash",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-1.5-flash"
        ],
        "requires_api_key": True,
        "requires_base_url": False,
        "default_base_url": "",
        "description": "Fast, high-rate limits, free tier supported via Google AI Studio."
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI",
        "default_model": "gpt-4o-mini",
        "models": [
            "gpt-4o-mini",
            "gpt-4o",
            "o3-mini",
            "o1"
        ],
        "requires_api_key": True,
        "requires_base_url": False,
        "default_base_url": "",
        "description": "Standard industry models with structured outputs."
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic Claude",
        "default_model": "claude-3-5-haiku-latest",
        "models": [
            "claude-3-5-haiku-latest",
            "claude-3-7-sonnet-latest",
            "claude-3-5-sonnet-latest"
        ],
        "requires_api_key": True,
        "requires_base_url": False,
        "default_base_url": "",
        "description": "Top-tier qualitative reasoning and risk analysis."
    },
    "groq": {
        "id": "groq",
        "name": "Groq Cloud",
        "default_model": "llama-3.3-70b-versatile",
        "models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768"
        ],
        "requires_api_key": True,
        "requires_base_url": False,
        "default_base_url": "",
        "description": "Ultra-low latency LPU inference with free tier."
    },
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek",
        "default_model": "deepseek-chat",
        "models": [
            "deepseek-chat",
            "deepseek-reasoner"
        ],
        "requires_api_key": True,
        "requires_base_url": False,
        "default_base_url": "https://api.deepseek.com",
        "description": "High-intelligence cost-effective models."
    },
    "ollama": {
        "id": "ollama",
        "name": "Ollama (Local)",
        "default_model": "llama3.2",
        "models": [
            "llama3.2",
            "deepseek-r1",
            "mistral",
            "qwen2.5"
        ],
        "requires_api_key": False,
        "requires_base_url": True,
        "default_base_url": "http://localhost:11434/v1",
        "description": "100% local offline self-hosted models."
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "default_model": "openai/gpt-4o-mini",
        "models": [
            "openai/gpt-4o-mini",
            "meta-llama/llama-3.3-70b-instruct",
            "anthropic/claude-3.5-haiku"
        ],
        "requires_api_key": True,
        "requires_base_url": True,
        "default_base_url": "https://openrouter.ai/api/v1",
        "description": "Unified routing across 100+ AI models."
    },
    "custom": {
        "id": "custom",
        "name": "Custom (OpenAI-Compatible)",
        "default_model": "custom-model",
        "models": [],
        "requires_api_key": False,
        "requires_base_url": True,
        "default_base_url": "http://localhost:8000/v1",
        "description": "Any vLLM, LMStudio, LocalAI or custom OpenAI-compatible server."
    }
}


def get_chat_model(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 600
) -> Optional[BaseChatModel]:
    """
    Factory function instantiating a LangChain BaseChatModel for the requested provider.
    """
    prov = (provider or "google").strip().lower()
    key = (api_key or "").strip().strip('"').strip("'")
    base = (base_url or "").strip()

    info = SUPPORTED_PROVIDERS.get(prov, SUPPORTED_PROVIDERS["custom"])
    target_model = (model_name or info.get("default_model", "gemini-3.1-flash")).strip()

    try:
        if prov == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            if not key:
                import os
                key = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
            if not key:
                logger.warning("Google GenAI requires API key. None provided.")
                return None
            return ChatGoogleGenerativeAI(
                model=target_model,
                google_api_key=key,
                temperature=temperature,
                max_output_tokens=max_tokens
            )

        elif prov == "openai":
            from langchain_openai import ChatOpenAI
            if not key:
                import os
                key = os.getenv("OPENAI_API_KEY", "")
            if not key:
                logger.warning("OpenAI requires API key. None provided.")
                return None
            return ChatOpenAI(
                model=target_model,
                api_key=key,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif prov == "anthropic":
            from langchain_anthropic import ChatAnthropic
            if not key:
                import os
                key = os.getenv("ANTHROPIC_API_KEY", "")
            if not key:
                logger.warning("Anthropic requires API key. None provided.")
                return None
            return ChatAnthropic(
                model_name=target_model,
                api_key=key,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif prov == "groq":
            from langchain_groq import ChatGroq
            if not key:
                import os
                key = os.getenv("GROQ_API_KEY", "")
            if not key:
                logger.warning("Groq requires API key. None provided.")
                return None
            return ChatGroq(
                model_name=target_model,
                groq_api_key=key,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif prov == "deepseek":
            from langchain_openai import ChatOpenAI
            if not key:
                import os
                key = os.getenv("DEEPSEEK_API_KEY", "")
            url = base or "https://api.deepseek.com"
            return ChatOpenAI(
                model=target_model or "deepseek-chat",
                api_key=key or "dummy",
                base_url=url,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif prov == "ollama":
            from langchain_openai import ChatOpenAI
            url = base or "http://localhost:11434/v1"
            return ChatOpenAI(
                model=target_model or "llama3.2",
                api_key=key or "ollama",
                base_url=url,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif prov == "openrouter":
            from langchain_openai import ChatOpenAI
            if not key:
                import os
                key = os.getenv("OPENROUTER_API_KEY", "")
            url = base or "https://openrouter.ai/api/v1"
            return ChatOpenAI(
                model=target_model or "openai/gpt-4o-mini",
                api_key=key or "dummy",
                base_url=url,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif prov == "custom":
            from langchain_openai import ChatOpenAI
            url = base or "http://localhost:8000/v1"
            return ChatOpenAI(
                model=target_model or "custom-model",
                api_key=key or "dummy",
                base_url=url,
                temperature=temperature,
                max_tokens=max_tokens
            )

        else:
            logger.warning(f"Unknown provider '{prov}', defaulting to OpenAI-compatible generic.")
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=target_model,
                api_key=key or "dummy",
                base_url=base or None,
                temperature=temperature,
                max_tokens=max_tokens
            )

    except Exception as e:
        logger.error(f"Error instantiating LangChain chat model ({prov}/{target_model}): {e}")
        return None


def extract_text_from_ai_message(content: Any) -> str:
    """
    Safely extracts clean plain-text string from LangChain AIMessage content,
    handling lists of dicts (e.g. [{'type': 'text', 'text': '...', 'extras': {...}}]),
    thought blocks, stringified Python reprs, and nested multi-turn structures.
    """
    if hasattr(content, "content"):
        content = content.content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "thought":
                    continue
                txt = item.get("text") or item.get("content") or ""
                parts.append(str(txt))
            elif isinstance(item, str):
                parts.append(item)
            elif hasattr(item, "text"):
                parts.append(str(item.text))
            else:
                parts.append(str(item))
        raw_text = "".join(parts).strip()
    elif isinstance(content, dict):
        if content.get("type") == "thought":
            raw_text = ""
        else:
            raw_text = str(content.get("text") or content.get("content") or "").strip()
    else:
        raw_text = str(content or "").strip()

    # Safety check: if raw_text itself is a stringified Python repr or JSON of a list or dict
    trimmed = raw_text.strip()
    if (trimmed.startswith("[") or trimmed.startswith("{")) and ("'text':" in trimmed or '"text":' in trimmed):
        try:
            import ast
            parsed = ast.literal_eval(trimmed)
            if isinstance(parsed, (list, dict)):
                return extract_text_from_ai_message(parsed)
        except Exception:
            try:
                import json
                parsed = json.loads(trimmed)
                if isinstance(parsed, (list, dict)):
                    return extract_text_from_ai_message(parsed)
            except Exception:
                pass

    return raw_text


async def execute_ai_completion(
    prompt: str,
    system_instruction: str = "",
    json_mode: bool = False,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    fallback_provider: Optional[str] = None,
    fallback_model: Optional[str] = None,
    fallback_api_key: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 600
) -> Optional[str]:
    """
    Executes an async completion through LangChain with automatic fallback chain failover.
    """
    primary = get_chat_model(
        provider=provider,
        model_name=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens
    )

    fallback = None
    if fallback_provider and (fallback_api_key or fallback_provider == "ollama"):
        fallback = get_chat_model(
            provider=fallback_provider,
            model_name=fallback_model,
            api_key=fallback_api_key,
            temperature=temperature,
            max_tokens=max_tokens
        )

    if not primary and not fallback:
        logger.warning("No valid primary or fallback AI models available for completion.")
        return None

    executable_model: BaseChatModel = primary or fallback # type: ignore
    if primary and fallback and primary != fallback:
        try:
            executable_model = primary.with_fallbacks([fallback])
        except Exception as e:
            logger.warning(f"Could not build LangChain with_fallbacks chain: {e}. Using primary directly.")
            executable_model = primary

    messages: List[BaseMessage] = []
    if system_instruction:
        messages.append(SystemMessage(content=system_instruction))
    
    prompt_text = prompt
    if json_mode and "json" not in prompt_text.lower():
        prompt_text += "\nRespond strictly in valid JSON format."
    messages.append(HumanMessage(content=prompt_text))

    try:
        response = await executable_model.ainvoke(messages)
        return extract_text_from_ai_message(response)
    except Exception as e:
        logger.warning(f"LangChain completion execution failed: {e}")
        # If primary failed and wasn't wrapped with fallback, try fallback manually
        if primary and fallback:
            try:
                logger.info("Attempting manual execution on secondary fallback model...")
                fb_res = await fallback.ainvoke(messages)
                return extract_text_from_ai_message(fb_res)
            except Exception as fb_err:
                logger.error(f"Fallback model execution also failed: {fb_err}")
        return None


async def test_ai_connection(
    provider: str,
    model_name: str,
    api_key: str = "",
    base_url: str = ""
) -> Dict[str, Any]:
    """
    Diagnostics ping testing provider credentials, base_url, and latency.
    """
    start = time.time()
    model = get_chat_model(
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0.1,
        max_tokens=50
    )

    if not model:
        return {
            "status": "error",
            "message": f"Could not initialize provider '{provider}'. Check API Key or endpoint URL."
        }

    try:
        messages = [
            SystemMessage(content="You are an automated diagnostic endpoint. Respond concisely."),
            HumanMessage(content="Ping! Respond with 'PONG' and your model identifier.")
        ]
        res = await model.ainvoke(messages)
        latency_ms = int((time.time() - start) * 1000)
        reply = extract_text_from_ai_message(res)
        return {
            "status": "ok",
            "provider": provider,
            "model": model_name,
            "latency_ms": latency_ms,
            "reply": reply
        }
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        logger.error(f"AI test connection error ({provider}/{model_name}): {e}")
        return {
            "status": "error",
            "provider": provider,
            "model": model_name,
            "latency_ms": latency_ms,
            "message": str(e)
        }
