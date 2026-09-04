import json
import time
import logging
import aiohttp
from typing import Optional, Dict, Any, List, Union

logger = logging.getLogger("CriptoBotEngine")

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_GEMINI_SEARCH_MODEL = "gemini-3.1-flash-lite"

# In-memory cache for Fear & Greed Index (1 hour TTL)
_fng_cache: Dict[str, Any] = {
    "data": None,
    "timestamp": 0.0
}

async def fetch_fear_and_greed_index() -> Dict[str, Any]:
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


import asyncio

HAS_GENAI_SDK = False
try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False

_model_cooldowns: Dict[str, float] = {}

def is_model_on_cooldown(model_name: str) -> bool:
    clean = str(model_name or "").replace("models/", "").strip()
    return time.time() < _model_cooldowns.get(clean, 0.0)

def record_model_cooldown(model_name: str, duration_sec: float = 600.0):
    clean = str(model_name or "").replace("models/", "").strip()
    _model_cooldowns[clean] = time.time() + duration_sec
    logger.info(f"Model {clean} placed on circuit-breaker cooldown for {duration_sec/60:.0f}m.")

def clear_model_cooldowns():
    global _model_cooldowns
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

async def fetch_available_gemini_models(api_key: str) -> List[str]:
    """
    Queries Google AI Studio to discover all valid generateContent models active on the user's account.
    Filters specifically for standard active gemini-* text generation models.
    """
    clean_key = str(api_key or "").strip().strip('"').strip("'")
    if not clean_key:
        return []

    if HAS_GENAI_SDK:
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
) -> Optional[str]:
    """
    Ultra-fast free secondary LLM caller targeting Groq's OpenAI-compatible endpoint.
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
        payload: Dict[str, Any] = {
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
) -> Optional[str]:
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
    if HAS_GENAI_SDK and not is_model_on_cooldown(clean_model):
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
        deadline = time.time() + 5.0  # Strict 5.0s maximum across Gemini attempts

        for m in healthy_models:
            if time.time() >= deadline:
                logger.info("Gemini call deadline exceeded (skipping remaining Gemini models).")
                break

            tools_to_try = [{"google_search": {}}] if (use_google_search and getattr(state, "enable_search_grounding", False)) else []

            while True:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={active_key}"
                gen_config: Dict[str, Any] = {
                    "temperature": 0.2 if json_mode else 0.3,
                    "maxOutputTokens": 600
                }
                if json_mode:
                    gen_config["responseMimeType"] = "application/json"

                payload: Dict[str, Any] = {
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
        res = await call_groq(prompt, groq_key, model=groq_model, system_instruction=system_instruction, json_mode=json_mode)
        if res:
            return res

    return None


async def fallback_trade_signal_analysis(
    pair: str,
    action: str,
    price: float,
    rsi: float,
    pct_b: float,
    reason: str,
    price_history: Optional[List[float]] = None,
    macro_sentiment: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Tier 0 Deterministic Mathematical Fallback for trade risk analysis.
    Produces accurate quantitative risk scores and stop loss values when cloud LLMs are unavailable.
    """
    fng = macro_sentiment or await fetch_fear_and_greed_index()
    fng_val = int(fng.get("value", 50))
    fng_class = str(fng.get("classification", "Neutral"))

    act = str(action or "BUY").upper()
    red_flags = []

    if act == "BUY":
        if rsi <= 32.0 and pct_b <= 0.25:
            verdict = "APPROVE"
            risk_score = 3
        elif rsi >= 65.0 or pct_b >= 0.85:
            verdict = "CAUTION"
            risk_score = 7
            red_flags.append(f"Momentum overextended (RSI: {rsi:.1f}, %B: {pct_b:.2f})")
        else:
            verdict = "APPROVE" if ("RSI" in reason.upper() or "DIP" in reason.upper()) else "CAUTION"
            risk_score = 4
    else:  # SELL
        if rsi >= 65.0:
            verdict = "APPROVE"
            risk_score = 3
        elif rsi <= 35.0:
            verdict = "CAUTION"
            risk_score = 7
            red_flags.append(f"Selling near support (RSI: {rsi:.1f})")
        else:
            verdict = "APPROVE"
            risk_score = 4

    if fng_val >= 80:
        red_flags.append("Extreme Market Greed")
    elif fng_val <= 20:
        red_flags.append("Extreme Market Fear")

    from engine.news_service import news_service
    if news_service.has_high_risk_event(pair):
        verdict = "HIGH_RISK"
        risk_score = 9
        red_flags.insert(0, "Breaking Emergency News Catalyst Detected")

    sl_map = {1: 2.0, 2: 2.0, 3: 2.5, 4: 3.0, 5: 3.5, 6: 4.0, 7: 4.5, 8: 5.0, 9: 6.0, 10: 7.0}
    suggested_sl = sl_map.get(risk_score, 3.0)

    summary = (
        f"[ALGO FALLBACK] Mathematical confluence: RSI {rsi:.1f}, %B {pct_b:.2f}, "
        f"Macro F&G {fng_val}/100 ({fng_class}). Risk calculated quantitatively."
    )

    return {
        "verdict": verdict,
        "risk_score": risk_score,
        "confidence": 0.82,
        "suggested_sl_percent": suggested_sl,
        "summary": summary,
        "red_flags": red_flags[:4],
        "fng_index": fng_val,
        "fng_classification": fng_class
    }


def fallback_scan_market_opportunities(
    market_context: Dict[str, Any],
    market_regime: Optional[str] = None,
    fng_str: str = "50/100 (Neutral)",
    fng_val: int = 50
) -> Dict[str, Any]:
    """
    Tier 0 Deterministic Screener Fallback for AI Opportunity Scout and Discord /opportunities.
    """
    prices = market_context.get("prices", {})
    indicators = market_context.get("indicators", {})
    fav_pairs = market_context.get("favorite_pairs", list(prices.keys()))
    opps = []

    for p in fav_pairs:
        pr = float(prices.get(p, 0.0))
        if pr <= 0.0:
            continue
        ind = indicators.get(p, {})
        rsi = float(ind.get("rsi", 50.0))
        pct_b = float(ind.get("pct_b", 0.5))

        if rsi <= 35.0 and pct_b <= 0.30:
            opps.append({
                "pair": p,
                "setup_type": "DIP_BUY",
                "confidence": 0.86,
                "key_levels": f"Support: ${pr*0.97:,.2f}, Target: ${pr*1.05:,.2f}",
                "analysis": f"[ALGO] Oversold pullback at RSI {rsi:.1f} and %B {pct_b:.2f} near lower Bollinger Band."
            })
        elif rsi >= 65.0 and pct_b >= 0.75:
            opps.append({
                "pair": p,
                "setup_type": "TAKE_PROFIT",
                "confidence": 0.84,
                "key_levels": f"Resistance: ${pr*1.03:,.2f}, Trailing Stop: ${pr*0.98:,.2f}",
                "analysis": f"[ALGO] Overextended runner at RSI {rsi:.1f} and %B {pct_b:.2f} near upper Bollinger Band."
            })

    # If no extreme threshold breached, surface best dip-buy candidate with relative oversoldness
    if not opps and fav_pairs:
        pairs_by_rsi = []
        for p in fav_pairs:
            pr = float(prices.get(p, 0.0))
            if pr > 0.0:
                ind = indicators.get(p, {})
                pairs_by_rsi.append((p, pr, float(ind.get("rsi", 50.0)), float(ind.get("pct_b", 0.5))))
        if pairs_by_rsi:
            pairs_by_rsi.sort(key=lambda x: x[2])
            best_pair, best_pr, best_rsi, best_b = pairs_by_rsi[0]
            if best_rsi < 48.0:
                opps.append({
                    "pair": best_pair,
                    "setup_type": "DIP_BUY",
                    "confidence": 0.78,
                    "key_levels": f"Support: ${best_pr*0.98:,.2f}, Target: ${best_pr*1.04:,.2f}",
                    "analysis": f"[ALGO] Consolidating pullback (RSI {best_rsi:.1f}, %B {best_b:.2f}) offering favorable risk-to-reward."
                })

    regime = market_regime or ("BULLISH_GREED" if fng_val >= 55 else ("BEARISH_FEAR" if fng_val <= 40 else "NEUTRAL"))
    return {
        "market_regime": regime,
        "fng_str": fng_str,
        "top_opportunities": opps[:5],
        "tactical_summary": "[ALGO FALLBACK] Quantitative technical screen active across watchlist momentum & Bollinger Bands."
    }


def fallback_news_synthesis(cached_items: List[Any]) -> Dict[str, Any]:
    """
    Tier 0 Deterministic News Digest Fallback synthesizing sentiment tags from headlines.
    """
    from dataclasses import asdict
    if not cached_items:
        return {
            "bullets": [
                "Market consolidating across major watchlist assets.",
                "RSI and Bollinger indicators within balanced trading range.",
                "No emergency breaking risk events detected across feeds."
            ],
            "overall_catalyst": "NEUTRAL",
            "headlines": []
        }

    bull_count = sum(1 for i in cached_items if getattr(i, "sentiment_tag", "") == "BULLISH")
    bear_count = sum(1 for i in cached_items if getattr(i, "sentiment_tag", "") == "BEARISH")
    risk_count = sum(1 for i in cached_items if getattr(i, "sentiment_tag", "") == "HIGH_RISK")

    if risk_count >= 1:
        catalyst = "HIGH_RISK"
    elif bull_count > bear_count:
        catalyst = "BULLISH"
    elif bear_count > bull_count:
        catalyst = "BEARISH"
    else:
        catalyst = "NEUTRAL"

    bullets = []
    for item in cached_items[:3]:
        tag = getattr(item, "sentiment_tag", "NEWS")
        asset = getattr(item, "asset", "MARKET")
        title = getattr(item, "title", "")
        bullets.append(f"[{tag}] {asset}: {title[:75]}")

    while len(bullets) < 3:
        bullets.append("Macro sentiment consolidating within normal technical volatility bands.")

    return {
        "bullets": bullets[:3],
        "overall_catalyst": catalyst,
        "headlines": [asdict(i) if hasattr(i, "__dataclass_fields__") else i for i in cached_items]
    }


async def analyze_trade_signal(
    pair: str,
    action: str,
    price: float,
    rsi: float,
    pct_b: float,
    reason: str,
    price_history: Optional[List[float]],
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    macro_sentiment: Optional[Dict[str, Any]] = None,
    extra_context: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Generates a structured quantitative risk evaluation for trade approval cards.
    Returns a dict with verdict ('APPROVE' | 'CAUTION' | 'HIGH_RISK'), risk_score (1-10),
    suggested_sl_percent, summary, and red_flags.
    """
    if not api_key:
        return None

    fng = macro_sentiment or await fetch_fear_and_greed_index()
    fng_val = fng.get("value", 50)
    fng_class = fng.get("classification", "Neutral")

    hist_str = ""
    if price_history and len(price_history) >= 5:
        recent = price_history[-5:]
        hist_str = f"Recent 5-period price trajectory: {['$' + str(round(p, 4)) for p in recent]}."

    prompt = f"""
Analyze this cryptocurrency trade signal and provide structured quantitative risk assessment:
- Pair: {pair}
- Action: {action}
- Current Price: ${price:,.4f}
- 14-period Wilder RSI: {rsi:.1f}
- Bollinger Band %B: {pct_b:.2f}
- Strategy Reason: {reason}
- Market Macro Sentiment: Fear & Greed Index is {fng_val}/100 ({fng_class})
{hist_str}

Return JSON with exact keys:
{{
  "verdict": "APPROVE" | "CAUTION" | "HIGH_RISK",
  "risk_score": 1 to 10 (integer: 1=safest, 10=highest risk),
  "confidence": float between 0.0 and 1.0,
  "suggested_sl_percent": float percentage for stop loss (e.g. 2.5),
  "summary": "1 to 2 sentences concise quantitative evaluation under 40 words",
  "red_flags": ["list", "of", "warning", "factors", "if any"]
}}
"""
    system_inst = "You are a senior quantitative crypto risk analyst. Be concise, objective, and return strictly valid JSON."
    raw_response = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst, json_mode=True)
    
    if not raw_response:
        logger.info(f"Using algorithmic risk evaluation fallback for {pair} {action}...")
        return await fallback_trade_signal_analysis(
            pair=pair, action=action, price=price, rsi=rsi, pct_b=pct_b,
            reason=reason, price_history=price_history, macro_sentiment=fng
        )

    try:
        # Clean any markdown formatting if present
        cleaned = raw_response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        data = json.loads(cleaned)
        
        # Validate and normalize fields
        verdict = str(data.get("verdict", "CAUTION")).upper()
        if verdict not in ["APPROVE", "CAUTION", "HIGH_RISK"]:
            verdict = "CAUTION"
            
        risk_score = int(data.get("risk_score", 5))
        risk_score = max(1, min(10, risk_score))
        
        suggested_sl = float(data.get("suggested_sl_percent", 3.0))
        confidence = float(data.get("confidence", 0.8))
        summary = str(data.get("summary", "Technical momentum analyzed."))[:300]
        red_flags = data.get("red_flags", [])
        if not isinstance(red_flags, list):
            red_flags = []

        # Check breaking news emergency override
        from engine.news_service import news_service
        if news_service.has_high_risk_event(pair):
            verdict = "HIGH_RISK"
            risk_score = 9
            red_flags.insert(0, "Breaking Emergency News Catalyst Detected")
            summary = f"HIGH RISK: Emergency news catalyst active for {pair}. Caution advised."

        return {
            "verdict": verdict,
            "risk_score": risk_score,
            "confidence": confidence,
            "suggested_sl_percent": round(suggested_sl, 2),
            "summary": summary,
            "red_flags": [str(rf) for rf in red_flags][:4],
            "fng_index": fng_val,
            "fng_classification": fng_class
        }
    except Exception as e:
        logger.warning(f"Failed to parse structured Gemini JSON: {e}. Raw: {raw_response[:100]}")
        # Fallback dictionary from raw response text
        return {
            "verdict": "CAUTION",
            "risk_score": 5,
            "confidence": 0.5,
            "suggested_sl_percent": 3.0,
            "summary": raw_response[:200].replace("\n", " ").strip(),
            "red_flags": [],
            "fng_index": fng_val,
            "fng_classification": fng_class
        }


async def summarize_news_insights(api_key: str, model: str = DEFAULT_GEMINI_MODEL, target_assets: Optional[List[str]] = None) -> Dict[str, Any]:
    from dataclasses import asdict
    from engine.news_service import news_service
    from engine.state import state
    
    if not target_assets:
        target_assets = [p.replace("USDT", "") for p in getattr(state, "favorite_pairs", ["BTC", "ETH", "SOL", "BNB"])]
    if not target_assets:
        target_assets = ["BTC", "ETH", "SOL", "BNB"]

    cached = await news_service.get_latest_news(target_assets, force_refresh=True)

    headline_lines = [f"- [{h.sentiment_tag}] {h.asset}: {h.title} (Source: {h.source})" for h in cached[:10]]
    headlines_text = "\n".join(headline_lines) if headline_lines else "No breaking items."

    prompt = f"""
Analyze these live cryptocurrency headlines and market events for {', '.join(target_assets)}:
{headlines_text}

Synthesize these findings into valid JSON:
{{
  "bullets": [
    "Specific key catalyst #1 summarized concisely",
    "Specific key catalyst #2 summarized concisely",
    "Specific key catalyst #3 summarized concisely"
  ],
  "overall_catalyst": "BULLISH" | "BEARISH" | "NEUTRAL"
}}
"""
    system_inst = "You are a senior quantitative financial news analyst. Return strictly valid JSON."
    raw = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst, json_mode=True, use_google_search=False)

    if not raw:
        return fallback_news_synthesis(cached)

    try:
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned)
        bullets = data.get("bullets", [])
        catalyst = data.get("overall_catalyst", "NEUTRAL")
        return {
            "bullets": [str(b) for b in bullets][:3],
            "overall_catalyst": str(catalyst).upper(),
            "headlines": [asdict(i) for i in cached]
        }
    except Exception:
        return fallback_news_synthesis(cached)


async def ask_gemini(
    query: str,
    market_context: Dict[str, Any],
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL
) -> str:
    """
    Answers user market or trading questions with complete live bot context, all favorite assets,
    active portfolio positions, cost bases, unrealized PnL, and macro sentiment.
    """
    if not api_key:
        return "[WARNING] Google AI Studio API key not configured. Add your free key in Web Companion Settings to enable AI features."

    fng = await fetch_fear_and_greed_index()
    fng_str = f"{fng.get('value', 50)}/100 ({fng.get('classification', 'Neutral')})"

    prices = market_context.get("prices", {})
    indicators = market_context.get("indicators", {})
    fav_pairs = market_context.get("favorite_pairs", list(prices.keys()))

    # Build comprehensive watchlist lines for ALL favorite pairs
    watchlist_lines = []
    for p in fav_pairs:
        pr = prices.get(p, 0.0)
        ind = indicators.get(p, {})
        rsi = ind.get("rsi", "N/A")
        pct_b = ind.get("pct_b", "N/A")
        watchlist_lines.append(f"- {p}: Price=${pr:,.4f}, RSI={rsi}, %B={pct_b}")

    watchlist_str = "\n".join(watchlist_lines) if watchlist_lines else "No active watchlist pairs."

    # Build active portfolio positions with cost bases & unrealized PnL
    portfolio = market_context.get("portfolio", {})
    cost_bases = market_context.get("cost_bases", {})
    position_lines = []
    for asset, qty in portfolio.items():
        if asset != "USDT" and qty > 0:
            pair = f"{asset}USDT"
            curr_p = prices.get(pair, 0.0)
            cost_b = cost_bases.get(pair, 0.0)
            if curr_p > 0 and (qty * curr_p) >= 1.0:
                pnl_pct = ((curr_p - cost_b) / cost_b * 100.0) if cost_b > 0 else 0.0
                pnl_usd = (curr_p - cost_b) * qty if cost_b > 0 else 0.0
                position_lines.append(f"- {asset}: {qty:.6f} @ Entry ${cost_b:,.4f} | Current ${curr_p:,.4f} | PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")

    positions_str = "\n".join(position_lines) if position_lines else "No open altcoin positions (100% USDT Reserve)."

    strategies = market_context.get("strategies", {})

    prompt = f"""
User Question: {query}

Live Bot Market Context:
- Crypto Fear & Greed Index: {fng_str}
- USDT Balance: ${market_context.get('usdt_balance', 0.0):.2f} (Testnet: {market_context.get('testnet', True)})
- Active Strategy Config: DCA Enabled: {strategies.get('dca_interval', 'N/A')}s, RSI Oversold: {strategies.get('rsi_threshold', 30.0)}, Bull Dip Threshold: {strategies.get('bull_rsi_threshold', 42.0)}, TP: +{strategies.get('tp_percent', 5.0)}%, Partial TP (50%): {strategies.get('partial_tp_percent', 4.0)}%, SL: -{strategies.get('sl_percent', 3.0)}%

- Current Open Portfolio Positions:
{positions_str}

- All Favorite Assets Technicals (Live Watchlist):
{watchlist_str}

Please answer the user's question clearly, incorporating live technicals, open position PnL, and macro sentiment across the entire watchlist where relevant. Keep response concise, actionable, and formatted nicely in Discord markdown.
"""
    system_inst = "You are Cripto-3DS AI Assistant, an expert quantitative cryptocurrency analyst and algorithmic trading assistant."
    result = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst, use_google_search=False)
    return result or "[AI OUTAGE] Google Gemini is currently experiencing high demand (HTTP 503). Configure a free Groq Cloud API key in Settings or .env (GROQ_API_KEY) for zero-downtime backup."


async def generate_market_briefing(
    market_context: Dict[str, Any],
    pnl_summary: Optional[Dict[str, Any]],
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL
) -> Dict[str, Any]:
    """
    Generates a structured morning market briefing incorporating macro sentiment,
    all watchlist technicals, and bot performance.
    """
    if not api_key:
        return {
            "headline": "Crypto Market Morning Intelligence",
            "fng_str": "N/A",
            "macro_regime": "[WARNING] Google AI Studio API key not configured. Add your key in Web Companion Settings to enable AI briefings.",
            "key_levels": "N/A",
            "strategy_recommendation": "Configure Gemini API key to activate daily quantitative market analysis.",
            "pnl_summary": pnl_summary or {}
        }

    fng = await fetch_fear_and_greed_index()
    fng_str = f"{fng.get('value', 50)}/100 ({fng.get('classification', 'Neutral')})"

    prices = market_context.get("prices", {})
    indicators = market_context.get("indicators", {})
    fav_pairs = market_context.get("favorite_pairs", list(prices.keys()))

    context_lines = []
    for p in fav_pairs:
        pr = prices.get(p, 0.0)
        ind = indicators.get(p, {})
        rsi = ind.get("rsi", "N/A")
        pct_b = ind.get("pct_b", "N/A")
        context_lines.append(f"{p}: Price=${pr:,.4f}, Wilder RSI={rsi}, %B={pct_b}")

    context_str = "\n".join(context_lines)

    pnl_info = ""
    if pnl_summary:
        pnl_info = f"Realized PnL: ${pnl_summary.get('total_pnl_usdt', 0.0):+.2f} USDT across {pnl_summary.get('closed_trades', 0)} closed trades (Win Rate: {pnl_summary.get('win_rate', 0.0)}%)."

    prompt = f"""
Generate a structured professional cryptocurrency morning market briefing:
- Crypto Fear & Greed Index: {fng_str}
- All Watchlist Technicals:
{context_str}
- Bot Account: USDT Balance: ${market_context.get('usdt_balance', 0.0):.2f}, {pnl_info}

Return JSON with exact keys:
{{
  "headline": "Punchy 1-line morning market outlook under 10 words",
  "macro_regime": "1-2 sentences on market macro phase, bull greed trends, and volatility",
  "key_levels": "Key support and resistance zones for top watchlist assets",
  "strategy_recommendation": "1-2 sentences actionable trading tactical advice for dip-buying and partial take-profit"
}}
"""
    system_inst = "You are a Chief Quantitative Crypto Strategist. Produce structured, highly accurate daily briefing JSON."
    raw_response = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst, json_mode=True)
    
    headline = "Crypto Market Morning Intelligence"
    macro = "Market consolidating across key levels."
    levels = "Support at recent swing lows; monitor RSI momentum."
    strategy = "Maintain discipline with adaptive dip buying and partial take profit."

    if raw_response:
        try:
            cleaned = raw_response.strip()
            if cleaned.startswith("```json"): cleaned = cleaned[7:]
            if cleaned.startswith("```"): cleaned = cleaned[3:]
            if cleaned.endswith("```"): cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            data = json.loads(cleaned)
            headline = data.get("headline", headline)
            macro = data.get("macro_regime", macro)
            levels = data.get("key_levels", levels)
            strategy = data.get("strategy_recommendation", strategy)
        except Exception as e:
            logger.warning(f"Briefing JSON parse error: {e}")

    return {
        "headline": headline,
        "fng_str": fng_str,
        "macro_regime": macro,
        "key_levels": levels,
        "strategy_recommendation": strategy,
        "pnl_summary": pnl_summary or {}
    }


async def scan_market_opportunities(
    market_context: Dict[str, Any],
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    market_regime: Optional[str] = None
) -> Dict[str, Any]:
    """
    Scans all watchlist assets to identify top quantitative setups:
    1. Bullish Dip-Buy pullbacks (healthy dip in uptrend)
    2. Partial Take-Profit / Overbought runners
    3. Momentum Breakouts
    """
    if not api_key:
        return {
            "market_regime": "NEUTRAL",
            "fng_str": "N/A",
            "top_opportunities": [],
            "tactical_summary": "[NOTE] Add Gemini API key to activate real-time AI opportunity scanner."
        }

    fng = await fetch_fear_and_greed_index()
    fng_val = fng.get("value", 50)
    fng_class = fng.get("classification", "Neutral")
    fng_str = f"{fng_val}/100 ({fng_class})"

    prices = market_context.get("prices", {})
    indicators = market_context.get("indicators", {})
    fav_pairs = market_context.get("favorite_pairs", list(prices.keys()))
    portfolio = market_context.get("portfolio", {})
    cost_bases = market_context.get("cost_bases", {})

    lines = []
    for p in fav_pairs:
        pr = prices.get(p, 0.0)
        ind = indicators.get(p, {})
        rsi = ind.get("rsi", 50.0)
        pct_b = ind.get("pct_b", 0.5)
        asset = p.replace("USDT", "")
        qty = portfolio.get(asset, 0.0)
        cost_b = cost_bases.get(p, 0.0)
        pos_str = f"Holdings: {qty:.4f} (Entry: ${cost_b:,.4f})" if qty > 0 and cost_b > 0 else "Holdings: None"
        lines.append(f"- {p}: Price=${pr:,.4f}, RSI={rsi}, %B={pct_b}, {pos_str}")

    watchlist_str = "\n".join(lines)

    regime_context = ""
    if market_regime:
        regime_context = f"\n- Current market regime: {market_regime}. Adjust setup identification accordingly:"

    prompt = f"""
Analyze this cryptocurrency watchlist and identify top trading opportunities right now:
- Market Macro: Fear & Greed Index is {fng_str}
- Live Watchlist Indicators & Positions:
{watchlist_str}{regime_context}

Evaluate and rank:
1. "DIP_BUY": Assets in bull/greed regime undergoing healthy pullbacks (RSI 35-45, %B <= 0.35, bouncing off support).
2. "TAKE_PROFIT": Assets overextended or reaching resistance (RSI > 65-70, %B > 0.85, profitable positions ready for 50% partial TP).
3. "MOMENTUM_BREAKOUT": Assets breaking out with high volume/confluence.

Return JSON strictly with format:
{{
  "market_regime": "BULLISH_GREED" | "NEUTRAL" | "BEARISH_FEAR",
  "top_opportunities": [
    {{
      "pair": "PAIR_SYMBOL",
      "setup_type": "DIP_BUY" | "TAKE_PROFIT" | "MOMENTUM_BREAKOUT",
      "confidence": float between 0.0 and 1.0,
      "key_levels": "Support: $X, Resistance: $Y",
      "analysis": "1 to 2 sentences concise quantitative explanation under 35 words"
    }}
  ],
  "tactical_summary": "1 to 2 sentences strategic summary on how to navigate today's market conditions."
}}
"""
    system_inst = "You are a Chief Quantitative Crypto Technical Analyst. Return strictly valid JSON ranking top actionable setups."
    raw = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst, json_mode=True)

    if not raw:
        return fallback_scan_market_opportunities(market_context, market_regime, fng_str, fng_val)

    try:
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned)
        opps = data.get("top_opportunities", [])
        return {
            "market_regime": data.get("market_regime", "NEUTRAL"),
            "fng_str": fng_str,
            "top_opportunities": opps[:5],
            "tactical_summary": data.get("tactical_summary", "Monitor key support and resistance zones.")
        }
    except Exception as e:
        logger.warning(f"Error parsing opportunities JSON: {e}")
        return fallback_scan_market_opportunities(market_context, market_regime, fng_str, fng_val)


async def evaluate_exit_momentum(
    pair: str,
    current_price: float,
    avg_entry_price: float,
    rsi: float,
    pct_b: float,
    price_history: List[float],
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL
) -> Dict[str, Any]:
    """
    Evaluates whether an overbought profitable position should trail its peak to ride a pump,
    or exit immediately due to momentum exhaustion.
    """
    profit_pct = ((current_price - avg_entry_price) / avg_entry_price) * 100.0 if avg_entry_price > 0 else 0.0
    if not api_key:
        return {
            "verdict": "TRAIL",
            "trail_delta_percent": 1.5,
            "momentum_phase": "STANDARD_TRAIL",
            "summary": "Trailing profit runner active (no API key configured)."
        }

    fng = await fetch_fear_and_greed_index()
    fng_val = fng.get("value", 50)
    fng_class = fng.get("classification", "Neutral")

    hist_str = ""
    if price_history:
        recent = price_history[-6:]
        hist_str = f"Recent 6-candle price trajectory: {['$' + str(round(p, 4)) for p in recent]}."

    prompt = f"""
Analyze this overbought cryptocurrency position to decide between TRAILING the pump vs SELLING NOW:
- Pair: {pair}
- Current Price: ${current_price:,.4f} (Entry: ${avg_entry_price:,.4f}, Current Profit: +{profit_pct:.2f}%)
- 14-period Wilder RSI: {rsi:.1f}
- Bollinger Band %B: {pct_b:.2f}
- Market Macro: Fear & Greed Index is {fng_val}/100 ({fng_class})
{hist_str}

Return JSON with exact keys:
{{
  "verdict": "TRAIL" | "SELL_NOW",
  "trail_delta_percent": float between 1.0 and 3.0 (e.g. 1.5 for standard, 1.0 for tight trailing, 2.5 for wide explosive trend),
  "momentum_phase": "PARABOLIC_BREAKOUT" | "EXHAUSTION" | "CHOPPY",
  "summary": "1 to 2 sentences concise quantitative explanation under 40 words"
}}
"""
    system_inst = "You are a quantitative momentum trading specialist. Evaluate if an overbought asset should trail higher or exit immediately."
    raw_response = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst, json_mode=True)

    if not raw_response:
        return {
            "verdict": "TRAIL",
            "trail_delta_percent": 1.5,
            "momentum_phase": "DEFAULT_TRAIL",
            "summary": "Default trailing stop runner active."
        }

    try:
        cleaned = raw_response.strip()
        if cleaned.startswith("```json"): cleaned = cleaned[7:]
        if cleaned.startswith("```"): cleaned = cleaned[3:]
        if cleaned.endswith("```"): cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        data = json.loads(cleaned)
        verdict = str(data.get("verdict", "TRAIL")).upper()
        if verdict not in ["TRAIL", "SELL_NOW"]:
            verdict = "TRAIL"

        trail_delta = float(data.get("trail_delta_percent", 1.5))
        trail_delta = max(0.8, min(4.0, trail_delta))

        return {
            "verdict": verdict,
            "trail_delta_percent": round(trail_delta, 2),
            "momentum_phase": str(data.get("momentum_phase", "PARABOLIC_BREAKOUT")),
            "summary": str(data.get("summary", "Momentum analyzed."))[:300]
        }
    except Exception as e:
        logger.warning(f"Failed to parse exit momentum JSON: {e}")
        return {
            "verdict": "TRAIL",
            "trail_delta_percent": 1.5,
            "momentum_phase": "FALLBACK_TRAIL",
            "summary": "Fallback trailing runner engaged."
        }