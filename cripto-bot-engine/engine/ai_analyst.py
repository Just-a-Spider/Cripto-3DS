import logging
import aiohttp
from typing import Optional, Dict, Any, List

logger = logging.getLogger("CriptoBotEngine")

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

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
                    "gemini-1.5-flash", "gemini-1.5-flash-latest",
                    "gemini-2.0-flash", "gemini-3.5-flash", 
                    "gemini-2.5-pro", "gemini-1.5-pro"
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

async def call_gemini(prompt: str, api_key: str, model: str = DEFAULT_GEMINI_MODEL, system_instruction: Optional[str] = None) -> Optional[str]:
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
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 600
            }
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
    model: str = DEFAULT_GEMINI_MODEL
) -> Optional[str]:
    """
    Generates a concise 2-sentence AI risk evaluation for trade approval cards.
    """
    if not api_key:
        return None

    hist_str = ""
    if price_history and len(price_history) >= 5:
        recent = price_history[-5:]
        hist_str = f"Recent price trajectory: {['$' + str(round(p, 4)) for p in recent]}."

    prompt = f"""
Analyze this cryptocurrency trade signal:
- Pair: {pair}
- Action: {action}
- Current Price: ${price:,.4f}
- 14-period Wilder RSI: {rsi:.1f}
- Bollinger Band %B: {pct_b:.2f}
- Strategy Reason: {reason}
{hist_str}

Provide a concise trade analysis:
1. One sentence evaluating technical risk/momentum.
2. One sentence with a verdict or caution rating (e.g. Risk: LOW / MEDIUM / HIGH).
Limit response to under 40 words total. Do not use markdown headers.
"""
    system_inst = "You are a professional quantitative crypto risk analyst. Be concise, verdict-driven, and objective."
    return await call_gemini(prompt, api_key, model=model, system_instruction=system_inst)


async def ask_gemini(
    query: str,
    market_context: Dict[str, Any],
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL
) -> str:
    """
    Answers user market or trading questions with live bot context.
    """
    if not api_key:
        return "⚠️ Google AI Studio API key not configured. Add your free key in Web Companion Settings to enable AI features."

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
{context_str}
USDT Balance: ${market_context.get('usdt_balance', 0.0):.2f}
Testnet: {market_context.get('testnet', True)}

Please answer the user's question clearly, incorporating live technical context where relevant. Keep response concise and readable in Discord chat.
"""
    system_inst = "You are Cripto-3DS AI Assistant, an expert quantitative cryptocurrency analyst and algorithmic trading assistant."
    result = await call_gemini(prompt, api_key, model=model, system_instruction=system_inst)
    return result or "⚠️ Gemini AI was unable to generate a response. Please try again."