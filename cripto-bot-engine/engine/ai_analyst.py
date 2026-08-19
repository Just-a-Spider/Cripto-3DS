import json
import time
import logging
import aiohttp
from typing import Optional, Dict, Any, List, Union

logger = logging.getLogger("CriptoBotEngine")

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

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


async def fetch_available_gemini_models(api_key: str) -> List[str]:
    """
    Queries Google AI Studio to discover all valid generateContent models active on the user's account.
    Filters specifically for standard gemini-* text generation models.
    """
    clean_key = str(api_key or "").strip().strip('"').strip("'")
    if not clean_key:
        return []

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
                    # Filter for standard Gemini text/multimodal generation models
                    if "generateContent" in methods and clean_name.startswith("gemini-"):
                        if not any(x in clean_name for x in ["embedding", "aqa", "imagen", "veo", "robotics"]):
                            models_list.append(clean_name)
                
                # Priority sorting: Flash models first, then Pro
                priority_order = [
                    "gemini-2.5-flash", "gemini-2.5-flash-lite", 
                    "gemini-3.1-flash-lite", "gemini-1.5-flash", 
                    "gemini-1.5-flash-latest", "gemini-2.0-flash", 
                    "gemini-3.5-flash", "gemini-2.5-pro", "gemini-1.5-pro"
                ]
                def sort_key(name: str):
                    try:
                        return (0, priority_order.index(name))
                    except ValueError:
                        return (1, name)

                return sorted(list(set(models_list)), key=sort_key)
    except Exception as e:
        logger.warning(f"Error querying Gemini models list: {e}")
        return []


async def call_gemini(
    prompt: str, 
    api_key: str, 
    model: str = DEFAULT_GEMINI_MODEL, 
    system_instruction: Optional[str] = None,
    json_mode: bool = False
) -> Optional[str]:
    """
    Direct asynchronous HTTP caller for Google AI Studio Gemini API with auto-fallback.
    Zero external SDK dependencies, pure aiohttp async execution.
    """
    clean_key = str(api_key or "").strip().strip('"').strip("'")
    if not clean_key:
        return None

    models_to_try = []
    clean_model = str(model or DEFAULT_GEMINI_MODEL).strip().replace("models/", "")
    if clean_model:
        models_to_try.append(clean_model)
    for fb in ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-1.5-flash"]:
        if fb not in models_to_try:
            models_to_try.append(fb)

    for m in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={clean_key}"
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

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=12.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                return parts[0].get("text", "").strip()
                    else:
                        err_text = await resp.text()
                        logger.warning(f"Gemini API model {m} returned HTTP {resp.status}: {err_text[:120]}")
                        # Try next fallback model
                        continue
        except aiohttp.ClientError as e:
            logger.warning(f"Gemini API network error on {m}: {e}")
        except Exception as e:
            logger.warning(f"Gemini API error on {m}: {type(e).__name__}: {e}")

    return None


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
        return None

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


async def ask_gemini(
    query: str,
    market_context: Dict[str, Any],
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL
) -> str:
    """
    Answers user market or trading questions with live bot context and macro sentiment.
    """
    if not api_key:
        return "⚠️ Google AI Studio API key not configured. Add your free key in Web Companion Settings to enable AI features."

    fng = await fetch_fear_and_greed_index()
    fng_str = f"{fng.get('value', 50)}/100 ({fng.get('classification', 'Neutral')})"

    context_lines = []
    prices = market_context.get("prices", {})
    indicators = market_context.get("indicators", {})
    for p, pr in list(prices.items())[:6]:
        ind = indicators.get(p, {})
        rsi = ind.get("rsi", "N/A")
        pct_b = ind.get("pct_b", "N/A")
        context_lines.append(f"{p}: Price=${pr}, RSI={rsi}, %B={pct_b}")

    context_str = "\n".join(context_lines)

    prompt = f"""
User Question: {query}

Live Bot Market Context:
- Crypto Fear & Greed Index: {fng_str}
- Watchlist Technicals:
{context_str}
- USDT Balance: ${market_context.get('usdt_balance', 0.0):.2f}
- Testnet Mode: {market_context.get('testnet', True)}

Please answer the user's question clearly, incorporating live technical and macro sentiment context where relevant. Keep response concise, actionable, and formatted nicely in Discord markdown.
"""
    system_inst = "You are Cripto-3DS AI Assistant, an expert quantitative cryptocurrency analyst and algorithmic trading assistant."
    result = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst)
    return result or "⚠️ Gemini AI was unable to generate a response. Please try again."


async def generate_market_briefing(
    market_context: Dict[str, Any],
    pnl_summary: Optional[Dict[str, Any]],
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL
) -> Dict[str, Any]:
    """
    Generates a structured morning market briefing incorporating macro sentiment,
    watchlist technicals, and bot performance.
    """
    if not api_key:
        return {
            "headline": "Crypto Market Morning Intelligence",
            "fng_str": "N/A",
            "macro_regime": "⚠️ Google AI Studio API key not configured. Add your key in Web Companion Settings to enable AI briefings.",
            "key_levels": "N/A",
            "strategy_recommendation": "Configure Gemini API key to activate daily quantitative market analysis.",
            "pnl_summary": pnl_summary or {}
        }

    fng = await fetch_fear_and_greed_index()
    fng_str = f"{fng.get('value', 50)}/100 ({fng.get('classification', 'Neutral')})"

    context_lines = []
    prices = market_context.get("prices", {})
    indicators = market_context.get("indicators", {})
    for p, pr in list(prices.items())[:6]:
        ind = indicators.get(p, {})
        rsi = ind.get("rsi", "N/A")
        pct_b = ind.get("pct_b", "N/A")
        context_lines.append(f"{p}: Price=${pr}, Wilder RSI={rsi}, %B={pct_b}")

    context_str = "\n".join(context_lines)

    pnl_info = ""
    if pnl_summary:
        pnl_info = f"Realized PnL: ${pnl_summary.get('total_pnl_usdt', 0.0):+.2f} USDT across {pnl_summary.get('closed_trades', 0)} closed trades (Win Rate: {pnl_summary.get('win_rate', 0.0)}%)."

    prompt = f"""
Generate a structured professional cryptocurrency morning market briefing:
- Crypto Fear & Greed Index: {fng_str}
- Watchlist Technicals:
{context_str}
- Bot Account: USDT Balance: ${market_context.get('usdt_balance', 0.0):.2f}, {pnl_info}

Return JSON with exact keys:
{{
  "headline": "Punchy 1-line morning market outlook under 10 words",
  "macro_regime": "1-2 sentences on market macro phase and volatility",
  "key_levels": "Key support and resistance zones for BTC, ETH, and top watchlist assets",
  "strategy_recommendation": "1-2 sentences actionable trading tactical advice for the day"
}}
"""
    system_inst = "You are a Chief Quantitative Crypto Strategist. Produce structured, highly accurate daily briefing JSON."
    raw_response = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst, json_mode=True)
    
    headline = "Crypto Market Morning Intelligence"
    macro = "Market consolidating across key levels."
    levels = "BTC support at recent lows; monitor RSI momentum."
    strategy = "Maintain discipline with strict stop losses and DCA pacing."

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