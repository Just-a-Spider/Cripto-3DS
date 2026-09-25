import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger("CriptoBotEngine")

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash"
DEFAULT_GEMINI_SEARCH_MODEL = "gemini-3.1-flash-lite"

# In-memory cache for Fear & Greed Index (1 hour TTL)
_fng_cache: dict[str, Any] = {
    "data": None,
    "timestamp": 0.0
}


async def fetch_fear_and_greed_index() -> dict[str, Any]:
    """
    Fetches the crypto market Fear & Greed Index from alternative.me API.
    Caches result in memory for 1 hour.
    """
    now = time.time()
    if _fng_cache["data"] and (now - _fng_cache["timestamp"] < 3600):
        return _fng_cache["data"]

    url = "https://api.alternative.me/fng/?limit=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    item = data.get("data", [{}])[0]
                    result = {
                        "value": int(item.get("value", 50)),
                        "classification": item.get("value_classification", "Neutral"),
                        "timestamp": int(item.get("timestamp", now))
                    }
                    _fng_cache["data"] = result
                    _fng_cache["timestamp"] = now
                    logger.info(f"Updated Fear & Greed Index: {result['value']} ({result['classification']})")
                    return result
    except Exception as e:
        logger.warning(f"Failed to fetch Fear & Greed Index: {e}")

    fallback = {"value": 50, "classification": "Neutral", "timestamp": int(now)}
    return _fng_cache["data"] or fallback


HAS_GENAI_SDK = False
try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False


def _has_genai_sdk() -> bool:
    import sys
    ai_mod = sys.modules.get("engine.ai_analyst")
    if ai_mod and hasattr(ai_mod, "HAS_GENAI_SDK"):
        return bool(getattr(ai_mod, "HAS_GENAI_SDK"))
    return HAS_GENAI_SDK

_model_cooldowns: dict[str, float] = {}


def is_model_on_cooldown(model_name: str) -> bool:
    clean = str(model_name or "").replace("models/", "").strip()
    return time.time() < _model_cooldowns.get(clean, 0.0)


def record_model_cooldown(model_name: str, duration_sec: float = 600.0):
    clean = str(model_name or "").replace("models/", "").strip()
    _model_cooldowns[clean] = time.time() + duration_sec
    logger.info(f"Model {clean} placed on circuit-breaker cooldown for {duration_sec/60:.0f}m.")


def clear_model_cooldowns():
    _model_cooldowns.clear()


ACTIVE_GEMINI_PRIORITY = [
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3-flash-preview"
]

ACTIVE_GROQ_PRIORITY = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768"
]


def _is_unsupported_model(name: str) -> bool:
    name_lower = name.lower()
    unsupported_substrings = [
        "embedding", "aqa", "imagen", "veo", "robotics", "tts", "computer-use", "image",
        "transcribe", "speech", "audio", "whisper", "live", "realtime", "dialogflow", "eval",
        "custom", "bilingual", "math", "code", "coder"
    ]
    if any(x in name_lower for x in unsupported_substrings):
        return True
    # Filter out models that return 404 or 429 quota on free tier (Pro models)
    if any(name_lower.startswith(x) for x in ["gemini-2.5", "gemini-2.0", "gemini-1.5", "gemini-3.1-pro", "gemini-pro"]):
        return True
    # Must be a text generateContent model
    if not (name_lower.startswith("gemini-") and ("flash" in name_lower or name_lower in ACTIVE_GEMINI_PRIORITY)):
        return True
    return False


async def fetch_available_gemini_models(api_key: str) -> list[str]:
    """
    Queries Google AI Studio to discover all valid generateContent models active on the user's account.
    Filters specifically for standard active gemini-* text generation models.
    """
    clean_key = str(api_key or "").strip().strip('"').strip("'")
    if not clean_key:
        return []

    if _has_genai_sdk():
        try:
            client = genai.Client(api_key=clean_key)
            loop = asyncio.get_running_loop()
            models_res = await loop.run_in_executor(None, lambda: list(client.models.list()))
            models_list = []
            for m in models_res:
                name = m.name.replace("models/", "").strip() if hasattr(m, "name") else ""
                if name.startswith("gemini-") and not _is_unsupported_model(name):
                    models_list.append(name)
            if models_list:
                def sort_key(name: str):
                    try:
                        return (0, ACTIVE_GEMINI_PRIORITY.index(name))
                    except ValueError:
                        return (1, name)
                return sorted(list(set(models_list)), key=sort_key)
        except Exception as e:
            logger.warning(f"Google GenAI SDK model list error (falling back to REST): {e}")

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_key}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to query Gemini models: HTTP {resp.status}")
                    return []
                data = await resp.json()
                models_list = []
                for m in data.get("models", []):
                    methods = m.get("supportedGenerationMethods", [])
                    raw_name = m.get("name", "")
                    clean_name = raw_name.replace("models/", "").strip()
                    if "generateContent" in methods and clean_name.startswith("gemini-"):
                        if not _is_unsupported_model(clean_name):
                            models_list.append(clean_name)

                def sort_key(name: str):
                    try:
                        return (0, ACTIVE_GEMINI_PRIORITY.index(name))
                    except ValueError:
                        return (1, name)

                return sorted(list(set(models_list)), key=sort_key)
    except Exception as e:
        logger.warning(f"Error querying Gemini models list: {e}")
        return []


async def call_groq(
    prompt: str,
    api_key: str,
    model: str = "llama-3.3-70b-versatile",
    system_instruction: str = "",
    json_mode: bool = False
) -> str | None:
    """
    Fast secondary LLM caller targeting Groq's OpenAI-compatible endpoint.
    Features automatic multi-model fallback across ACTIVE_GROQ_PRIORITY.
    """
    clean_key = str(api_key or "").strip().strip('"').strip("'")
    if not clean_key:
        return None

    models_to_try = [model or "llama-3.3-70b-versatile"]
    for alt in ACTIVE_GROQ_PRIORITY:
        if alt not in models_to_try:
            models_to_try.append(alt)

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {clean_key}",
        "Content-Type": "application/json"
    }
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    for target_model in models_to_try[:2]:
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.2
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        choices = data.get("choices", [])
                        if choices:
                            content = choices[0].get("message", {}).get("content", "").strip()
                            if content:
                                return content
                    else:
                        err = await resp.text()
                        logger.warning(f"Groq API model {target_model} returned HTTP {resp.status}: {err[:120]}")
        except Exception as e:
            logger.warning(f"Groq API call error on {target_model}: {e}")
    return None


async def call_gemini(
    prompt: str,
    api_key: str,
    model: str = "",
    system_instruction: str = "",
    json_mode: bool = False,
    use_google_search: bool = False
) -> str | None:
    """
    Caller for Google AI Studio Gemini API using native google.genai SDK with HTTP auto-fallback.
    Features circuit breaker, 503/429 cooldowns, strict total execution deadline,
    and automatic fallback to Groq secondary free provider if configured.
    """
    clean_key = str(api_key or "").strip().strip('"').strip("'")
    if not clean_key:
        return None

    from engine.state import state

    # Free tier safety: avoid websearch tool unless explicitly enabled
    if use_google_search and not getattr(state, "enable_search_grounding", False):
        use_google_search = False

    if not model:
        if use_google_search:
            model = getattr(state, "gemini_search_model", DEFAULT_GEMINI_SEARCH_MODEL)
        else:
            model = getattr(state, "gemini_model", DEFAULT_GEMINI_MODEL)

    clean_model = str(model or DEFAULT_GEMINI_MODEL).strip().replace("models/", "")

    raw_keys = [k.strip() for k in clean_key.split(",") if k.strip()]
    active_key = raw_keys[0] if raw_keys else clean_key

    # 1. Native google.genai SDK attempt (if not on cooldown)
    if _has_genai_sdk() and not is_model_on_cooldown(clean_model):
        try:
            client = genai.Client(api_key=active_key)
            config_args = {
                "temperature": 0.2 if json_mode else 0.3,
                "max_output_tokens": 600
            }
            if json_mode:
                config_args["response_mime_type"] = "application/json"
            if system_instruction:
                config_args["system_instruction"] = system_instruction
            if use_google_search and getattr(state, "enable_search_grounding", False):
                config_args["tools"] = [types.Tool(google_search=types.GoogleSearch())]

            config = types.GenerateContentConfig(**config_args)
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=clean_model,
                        contents=prompt,
                        config=config
                    )
                ),
                timeout=4.5
            )
            if response and response.text:
                return response.text.strip()
        except asyncio.TimeoutError:
            record_model_cooldown(clean_model, 600.0)
            logger.warning(f"GenAI SDK timeout on {clean_model} (cooldown 10m)")
        except Exception as e:
            err_str = str(e).lower()
            if "503" in err_str or "high demand" in err_str or "429" in err_str:
                record_model_cooldown(clean_model, 600.0)
            logger.debug(f"Google GenAI SDK on {clean_model} (switching to HTTP fallback): {e}")

    # Build strictly vetted candidate model list (never try arbitrary or non-text models)
    candidate_pool = [clean_model]
    for fb in ACTIVE_GEMINI_PRIORITY:
        if fb not in candidate_pool and not _is_unsupported_model(fb):
            candidate_pool.append(fb)

    # Filter for healthy models not on circuit-breaker cooldown
    healthy_models = [m for m in candidate_pool if not is_model_on_cooldown(m) and not _is_unsupported_model(m)]

    # Cap to at most 2 attempts (primary + 1 fallback) during outages
    healthy_models = healthy_models[:2]

    if not healthy_models:
        logger.info("All Gemini models currently on circuit-breaker cooldown. Bypassing Gemini to backup provider...")
    else:
        deadline = time.time() + 5.0

        for m in healthy_models:
            if time.time() >= deadline:
                logger.info("Gemini call deadline exceeded (skipping remaining Gemini models).")
                break

            tools_to_try = [{"google_search": {}}] if (use_google_search and getattr(state, "enable_search_grounding", False)) else []

            while True:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={active_key}"
                gen_config: dict[str, Any] = {
                    "temperature": 0.2 if json_mode else 0.3,
                    "maxOutputTokens": 600
                }
                if json_mode:
                    gen_config["responseMimeType"] = "application/json"

                payload: dict[str, Any] = {
                    "contents": [
                        {
                            "parts": [{"text": prompt}]
                        }
                    ],
                    "generationConfig": gen_config
                }
                if system_instruction:
                    payload["system_instruction"] = {
                        "parts": [{"text": system_instruction}]
                    }
                if tools_to_try:
                    payload["tools"] = tools_to_try

                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                candidates = data.get("candidates", [])
                                if candidates:
                                    parts = candidates[0].get("content", {}).get("parts", [])
                                    if parts:
                                        return parts[0].get("text", "").strip()
                            else:
                                err_text = await resp.text()
                                if tools_to_try and (resp.status in [400, 429] or "quota" in err_text.lower()):
                                    logger.info(f"Gemini model {m} search tool rate-limited (HTTP {resp.status}). Retrying as pure text...")
                                    tools_to_try = []
                                    continue

                                if resp.status in (429, 503) or "high demand" in err_text.lower():
                                    record_model_cooldown(m, 600.0)

                                logger.warning(f"Gemini API model {m} returned HTTP {resp.status}: {err_text[:120]}")
                                break
                except asyncio.TimeoutError:
                    record_model_cooldown(m, 600.0)
                    logger.warning(f"Gemini API network timeout on {m} (cooled down for 10m)")
                    break
                except aiohttp.ClientError as e:
                    logger.warning(f"Gemini API network error on {m}: {e}")
                    break
                except Exception as e:
                    logger.warning(f"Gemini API error on {m}: {type(e).__name__}: {e}")
                    break

    # Secondary Free Provider Fallback: Groq (if configured)
    groq_key = getattr(state, "groq_api_key", "")
    if groq_key:
        logger.info("Delegating to Groq secondary free LLM fallback...")
        groq_model = getattr(state, "groq_model", "llama-3.3-70b-versatile")
        import sys
        ai_mod = sys.modules.get("engine.ai_analyst")
        groq_fn = getattr(ai_mod, "call_groq", call_groq) if ai_mod else call_groq
        res = await groq_fn(prompt, groq_key, model=groq_model, system_instruction=system_instruction, json_mode=json_mode)
        if res:
            return res

    return None


_ORIGINAL_CALL_GEMINI = call_gemini


async def call_ai(
    prompt: str,
    system_instruction: str = "",
    json_mode: bool = False,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    use_google_search: bool = False
) -> str | None:
    """
    Unified LangChain AI invoker supporting all configured providers.
    Transparently honors monkeypatched call_gemini or call_groq in test environments.
    """
    import sys
    this_mod = sys.modules.get("engine.ai_analyst")
    if this_mod and getattr(this_mod, "call_gemini", None) != _ORIGINAL_CALL_GEMINI:
        return await this_mod.call_gemini(
            prompt,
            api_key=api_key or "",
            model=model or "",
            system_instruction=system_instruction,
            json_mode=json_mode,
            use_google_search=use_google_search
        )

    from engine.ai_provider import execute_ai_completion
    from engine.state import state

    target_prov = (provider or getattr(state, "ai_provider", "google")).strip().lower()
    target_model = model or getattr(state, "ai_model", DEFAULT_GEMINI_MODEL)
    target_key = api_key or getattr(state, "ai_api_key", "") or (getattr(state, "gemini_api_key", "") if target_prov == "google" else "")
    target_base = base_url or getattr(state, "ai_base_url", "")
    fb_prov = getattr(state, "ai_fallback_provider", "groq")
    fb_model = getattr(state, "ai_fallback_model", "llama-3.3-70b-versatile")
    fb_key = getattr(state, "ai_fallback_api_key", "") or getattr(state, "groq_api_key", "")

    res = await execute_ai_completion(
        prompt=prompt,
        system_instruction=system_instruction,
        json_mode=json_mode,
        provider=target_prov,
        model=target_model,
        api_key=target_key,
        base_url=target_base,
        fallback_provider=fb_prov,
        fallback_model=fb_model,
        fallback_api_key=fb_key
    )
    if res:
        return res

    # Fallback to legacy REST call_gemini if google
    if target_prov == "google" and target_key:
        return await _ORIGINAL_CALL_GEMINI(
            prompt,
            target_key,
            model=target_model,
            system_instruction=system_instruction,
            json_mode=json_mode,
            use_google_search=use_google_search
        )

    return None
