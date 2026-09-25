import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_gemini_analyst_fallback():
    from engine.ai_analyst import analyze_trade_signal, ask_gemini

    # 1. When no key provided -> returns None gracefully
    analysis = await analyze_trade_signal(
        pair="BTCUSDT", action="BUY", price=62000.0, rsi=25.0, pct_b=0.1,
        reason="RSI Oversold", price_history=[63000.0, 62500.0, 62000.0], api_key=""
    )
    assert analysis is None

    # 2. ask_gemini fallback message
    ans = await ask_gemini("What is RSI?", {}, api_key="")
    assert "Google AI Studio API key not configured" in ans

def test_gemini_state_and_config():
    from engine.state import state
    d = state.to_dict()
    assert "gemini_model" in d
    assert "gemini_search_model" in d
    assert "has_gemini" in d
    assert "available_gemini_models" in d
    assert "enable_search_grounding" in d
    assert "has_groq" in d
    assert d["gemini_model"] == "gemini-3.1-flash-lite"
    assert d["gemini_search_model"] == "gemini-3.1-flash-lite"
    assert d["enable_search_grounding"] is False

@pytest.mark.asyncio
async def test_structured_ai_risk_parsing(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import analyze_trade_signal

    # Mock call_gemini to return a valid JSON string
    async def mock_call_gemini(*args, **kwargs):
        return """```json
{
  "verdict": "APPROVE",
  "risk_score": 3,
  "confidence": 0.9,
  "suggested_sl_percent": 2.5,
  "summary": "Strong oversold bounce setup with bullish volume convergence.",
  "red_flags": []
}
```"""
    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    res = await analyze_trade_signal(
        pair="BTCUSDT", action="BUY", price=62000.0, rsi=28.0, pct_b=-0.05,
        reason="RSI Oversold", price_history=[63000, 62500, 62000], api_key="test_key"
    )

    assert isinstance(res, dict)
    assert res["verdict"] == "APPROVE"
    assert res["risk_score"] == 3
    assert res["suggested_sl_percent"] == 2.5
    assert "oversold bounce" in res["summary"].lower()
    assert "fng_index" in res

@pytest.mark.asyncio
async def test_market_briefing(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import generate_market_briefing
    from engine.notifier import build_briefing_embed

    # 1. Test fallback when no key provided
    fallback_data = await generate_market_briefing({}, None, api_key="")
    assert "headline" in fallback_data
    assert "macro_regime" in fallback_data

    # 2. Test mocked Gemini briefing response
    async def mock_call_gemini(*args, **kwargs):
        return """```json
{
  "headline": "Bitcoin Consolidates Ahead of Volatility",
  "macro_regime": "Market in low-volatility compression phase.",
  "key_levels": "BTC support at $64,200, resistance at $68,500.",
  "strategy_recommendation": "Maintain standard DCA pacing with tight SL."
}
```"""
    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    mock_state = {
        "prices": {"BTCUSDT": 65000.0, "ETHUSDT": 3400.0},
        "indicators": {"BTCUSDT": {"rsi": 48.0, "pct_b": 0.52}},
        "usdt_balance": 500.0
    }
    pnl = {"total_pnl_usdt": 45.20, "win_rate": 80.0, "closed_trades": 5}
    briefing = await generate_market_briefing(mock_state, pnl, api_key="valid_key")

    assert briefing["headline"] == "Bitcoin Consolidates Ahead of Volatility"
    assert "BTC support at $64,200" in briefing["key_levels"]
    assert "low-volatility compression" in briefing["macro_regime"]

    # 3. Test embed construction
    embed = build_briefing_embed(briefing, "gemini-2.5-flash")
    assert embed is not None
    assert "Bitcoin Consolidates" in embed.title

@pytest.mark.asyncio
async def test_evaluate_exit_momentum(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import evaluate_exit_momentum

    # 1. Test fallback when no key
    res_no_key = await evaluate_exit_momentum("ETHUSDT", 2000.0, 1800.0, 78.0, 1.15, [], api_key="")
    assert res_no_key["verdict"] == "TRAIL"
    assert res_no_key["trail_delta_percent"] == 1.5

    # 2. Test mocked Gemini momentum response
    async def mock_call_gemini(*args, **kwargs):
        return """```json
{
  "verdict": "TRAIL",
  "trail_delta_percent": 1.2,
  "momentum_phase": "PARABOLIC_BREAKOUT",
  "summary": "Strong continuous green candles. Trail peak with 1.2% delta."
}
```"""
    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    res = await evaluate_exit_momentum("ETHUSDT", 2050.0, 1877.0, 81.2, 1.22, [1900, 1950, 2000, 2050], api_key="valid_key")
    assert res["verdict"] == "TRAIL"
    assert res["trail_delta_percent"] == 1.2
    assert res["momentum_phase"] == "PARABOLIC_BREAKOUT"

@pytest.mark.asyncio
async def test_gemini_unsupported_model_filter():
    from engine.ai_analyst import _is_unsupported_model
    assert _is_unsupported_model("gemini-2.5-flash") is True
    assert _is_unsupported_model("gemini-2.0-flash") is True
    assert _is_unsupported_model("gemini-1.5-flash") is True
    assert _is_unsupported_model("gemini-3.1-flash-lite-tts") is True
    assert _is_unsupported_model("gemini-3.5-transcribe") is True
    assert _is_unsupported_model("gemini-3.5-audio") is True
    assert _is_unsupported_model("gemini-3.5-live") is True
    assert _is_unsupported_model("gemini-3.1-flash-lite") is False
    assert _is_unsupported_model("gemini-3.5-flash") is False
    assert _is_unsupported_model("gemini-flash-lite-latest") is False

@pytest.mark.asyncio
async def test_gemini_call_fallback(monkeypatch):
    import aiohttp

    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import call_gemini

    monkeypatch.setattr(ai_mod, "HAS_GENAI_SDK", False)

    called = []
    def mock_post(self, url, json=None, timeout=None):
        called.append(url)
        class MockResp:
            status = 200
            async def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
            async def text(self):
                return "OK"
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        return MockResp()

    monkeypatch.setattr(aiohttp.ClientSession, "post", mock_post)

    res = await call_gemini("Test", "test_key", model="gemini-3.1-flash-lite")
    assert res == "OK"
    assert len(called) > 0

@pytest.mark.asyncio
async def test_ask_gemini_full_watchlist_and_positions_context(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import ask_gemini

    captured_prompt = []
    async def mock_call_gemini(prompt, api_key, model=None, system_instruction=None, json_mode=False, use_google_search=False):
        captured_prompt.append(prompt)
        return "Market analysis complete."

    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    mock_state = {
        "favorite_pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT"],
        "prices": {
            "BTCUSDT": 65000.0,
            "ETHUSDT": 3200.0,
            "SOLUSDT": 180.0,
            "BNBUSDT": 580.0,
            "XRPUSDT": 0.60,
            "ADAUSDT": 0.45,
            "AVAXUSDT": 28.0,
            "NEARUSDT": 5.20
        },
        "indicators": {
            "BTCUSDT": {"rsi": 55.0, "pct_b": 0.6},
            "ETHUSDT": {"rsi": 48.0, "pct_b": 0.4},
            "SOLUSDT": {"rsi": 41.0, "pct_b": 0.25},
            "BNBUSDT": {"rsi": 62.0, "pct_b": 0.75},
            "XRPUSDT": {"rsi": 71.0, "pct_b": 0.90},
            "ADAUSDT": {"rsi": 38.0, "pct_b": 0.20},
            "AVAXUSDT": {"rsi": 44.0, "pct_b": 0.30},
            "NEARUSDT": {"rsi": 50.0, "pct_b": 0.50}
        },
        "portfolio": {
            "USDT": 500.0,
            "SOL": 2.5,
            "XRP": 500.0
        },
        "cost_bases": {
            "SOLUSDT": 150.0,
            "XRPUSDT": 0.50
        },
        "usdt_balance": 500.0,
        "testnet": True,
        "strategies": {
            "dca_interval": 3600,
            "rsi_threshold": 30.0,
            "bull_rsi_threshold": 42.0,
            "tp_percent": 5.0,
            "partial_tp_percent": 4.0,
            "sl_percent": 3.0
        }
    }

    ans = await ask_gemini("Should I take profit on my positions?", mock_state, api_key="valid_key")
    assert ans == "Market analysis complete."
    assert len(captured_prompt) == 1
    prompt_text = captured_prompt[0]

    # Verify all 8 pairs are included (no [:6] truncation!)
    for pair in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT"]:
        assert pair in prompt_text

    # Verify open positions with unrealized PnL are included
    assert "SOL: 2.500000 @ Entry $150.0000" in prompt_text
    assert "XRP: 500.000000 @ Entry $0.5000" in prompt_text

@pytest.mark.asyncio
async def test_scan_market_opportunities(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import scan_market_opportunities

    # 1. Fallback when no key
    res_no_key = await scan_market_opportunities({}, api_key="")
    assert res_no_key["market_regime"] == "NEUTRAL"
    assert len(res_no_key["top_opportunities"]) == 0

    # 2. Mocked Gemini response
    async def mock_call_gemini(prompt, api_key, model=None, system_instruction=None, json_mode=False, use_google_search=False):
        return """```json
{
  "market_regime": "BULLISH_GREED",
  "top_opportunities": [
    {
      "pair": "SOLUSDT",
      "setup_type": "DIP_BUY",
      "confidence": 0.88,
      "key_levels": "Support: $170.00, Resistance: $192.00",
      "analysis": "RSI pulled back to 41 near lower Bollinger Band with strong macro greed support."
    },
    {
      "pair": "XRPUSDT",
      "setup_type": "TAKE_PROFIT",
      "confidence": 0.82,
      "key_levels": "Resistance: $0.62",
      "analysis": "RSI 71 overbought testing resistance. Partial 50% TP recommended."
    }
  ],
  "tactical_summary": "Buy minor dips on large caps and secure partial profits on overextended runners."
}
```"""
    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    mock_state = {
        "favorite_pairs": ["SOLUSDT", "XRPUSDT"],
        "prices": {"SOLUSDT": 172.0, "XRPUSDT": 0.61},
        "indicators": {
            "SOLUSDT": {"rsi": 41.0, "pct_b": 0.22},
            "XRPUSDT": {"rsi": 71.0, "pct_b": 0.88}
        },
        "portfolio": {"SOL": 2.0},
        "cost_bases": {"SOLUSDT": 160.0}
    }

    data = await scan_market_opportunities(mock_state, api_key="valid_key")
    assert data["market_regime"] == "BULLISH_GREED"
    assert len(data["top_opportunities"]) == 2
    assert data["top_opportunities"][0]["setup_type"] == "DIP_BUY"
    assert data["top_opportunities"][1]["setup_type"] == "TAKE_PROFIT"
    assert "Buy minor dips" in data["tactical_summary"]

@pytest.mark.asyncio
async def test_gemini_503_circuit_breaker_and_cooldown():
    from engine.ai_analyst import clear_model_cooldowns, is_model_on_cooldown, record_model_cooldown

    clear_model_cooldowns()
    assert not is_model_on_cooldown("gemini-3.5-flash-lite")

    # Record 503 high demand cooldown
    record_model_cooldown("gemini-3.5-flash-lite", duration_sec=600.0)
    assert is_model_on_cooldown("gemini-3.5-flash-lite")
    assert is_model_on_cooldown("models/gemini-3.5-flash-lite")

    clear_model_cooldowns()
    assert not is_model_on_cooldown("gemini-3.5-flash-lite")

@pytest.mark.asyncio
async def test_trade_signal_algorithmic_fallback_on_outage(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import analyze_trade_signal

    # Simulate Gemini total outage (returning None)
    async def mock_call_gemini(*args, **kwargs):
        return None

    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    res = await analyze_trade_signal(
        pair="BTCUSDT",
        action="BUY",
        price=64000.0,
        rsi=28.0,
        pct_b=0.15,
        reason="Wilder RSI Oversold Bounce",
        price_history=[65000, 64500, 64000],
        api_key="mock_free_key"
    )

    assert isinstance(res, dict)
    assert res["verdict"] == "APPROVE"
    assert res["risk_score"] <= 4
    assert res["suggested_sl_percent"] > 0
    assert "[ALGO FALLBACK]" in res["summary"]
    assert "fng_index" in res

@pytest.mark.asyncio
async def test_scout_algorithmic_fallback_on_outage(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import scan_market_opportunities

    # Simulate Gemini outage
    async def mock_call_gemini(*args, **kwargs):
        return None

    monkeypatch.setattr(ai_mod, "call_gemini", mock_call_gemini)

    mock_state = {
        "prices": {"BTCUSDT": 64000.0, "ETHUSDT": 3200.0},
        "favorite_pairs": ["BTCUSDT", "ETHUSDT"],
        "indicators": {
            "BTCUSDT": {"rsi": 28.0, "pct_b": 0.15},
            "ETHUSDT": {"rsi": 72.0, "pct_b": 0.88}
        }
    }

    res = await scan_market_opportunities(mock_state, api_key="mock_free_key")
    assert isinstance(res, dict)
    opps = res.get("top_opportunities", [])
    assert len(opps) >= 1
    types = [o["setup_type"] for o in opps]
    assert "DIP_BUY" in types or "TAKE_PROFIT" in types
    assert "[ALGO FALLBACK]" in res.get("tactical_summary", "")

@pytest.mark.asyncio
async def test_groq_fallback_integration(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.state import state

    # Mock call_groq returning valid JSON
    async def mock_call_groq(*args, **kwargs):
        return '{"verdict": "APPROVE", "risk_score": 2, "confidence": 0.95, "suggested_sl_percent": 2.0, "summary": "Groq Llama-3.3-70b evaluated oversold confluence.", "red_flags": []}'

    monkeypatch.setattr(ai_mod, "call_groq", mock_call_groq)
    # Ensure Gemini returns None so Groq is reached
    monkeypatch.setattr(ai_mod, "HAS_GENAI_SDK", False)

    state.groq_api_key = "gsk_test123"
    try:
        raw = await ai_mod.call_gemini("test prompt", api_key="invalid_gemini_key")
        assert raw is not None
        assert "Groq Llama-3.3-70b" in raw
    finally:
        state.groq_api_key = ""

@pytest.mark.asyncio
async def test_gemini_outage_capped_models_and_fast_bypass(monkeypatch):
    import engine.ai_analyst as ai_mod
    from engine.ai_analyst import ACTIVE_GEMINI_PRIORITY, call_gemini, clear_model_cooldowns, record_model_cooldown

    clear_model_cooldowns()
    monkeypatch.setattr(ai_mod, "HAS_GENAI_SDK", False)

    # Place all active Gemini models on cooldown (simulating widespread 503)
    for m in ACTIVE_GEMINI_PRIORITY:
        record_model_cooldown(m, 600.0)

    # With all on cooldown, call_gemini must immediately bypass without making HTTP calls
    import aiohttp
    called = []
    def mock_post(*args, **kwargs):
        called.append(args)
        raise RuntimeError("Should not be called")

    monkeypatch.setattr(aiohttp.ClientSession, "post", mock_post)

    res = await call_gemini("test prompt", api_key="test_key", model="gemini-3.1-flash-lite")
    # Must return None (or Groq if configured) with 0 HTTP calls
    assert res is None
    assert len(called) == 0

    clear_model_cooldowns()

@pytest.mark.asyncio
async def test_langchain_provider_factory():
    from langchain_anthropic import ChatAnthropic
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_groq import ChatGroq
    from langchain_openai import ChatOpenAI

    from engine.ai_provider import get_chat_model

    # 1. Google
    m_google = get_chat_model("google", model_name="gemini-3.1-flash", api_key="test_google_key")
    assert isinstance(m_google, ChatGoogleGenerativeAI)
    assert m_google.model == "gemini-3.1-flash"

    # 2. OpenAI
    m_openai = get_chat_model("openai", model_name="gpt-4o-mini", api_key="sk-test")
    assert isinstance(m_openai, ChatOpenAI)
    assert m_openai.model_name == "gpt-4o-mini"

    # 3. Anthropic
    m_anthropic = get_chat_model("anthropic", model_name="claude-3-5-haiku-latest", api_key="sk-ant-test")
    assert isinstance(m_anthropic, ChatAnthropic)
    assert m_anthropic.model == "claude-3-5-haiku-latest"

    # 4. Groq
    m_groq = get_chat_model("groq", model_name="llama-3.3-70b-versatile", api_key="gsk-test")
    assert isinstance(m_groq, ChatGroq)
    assert m_groq.model_name == "llama-3.3-70b-versatile"

    # 5. Ollama
    m_ollama = get_chat_model("ollama", model_name="llama3.2", base_url="http://localhost:11434/v1")
    assert isinstance(m_ollama, ChatOpenAI)
    assert "11434" in str(m_ollama.openai_api_base)
    assert m_ollama.model_name == "llama3.2"

    # 6. DeepSeek
    m_deepseek = get_chat_model("deepseek", model_name="deepseek-chat", api_key="sk-ds-test")
    assert isinstance(m_deepseek, ChatOpenAI)
    assert "deepseek.com" in str(m_deepseek.openai_api_base)

@pytest.mark.asyncio
async def test_langchain_execute_ai_completion_fallback(monkeypatch):
    from langchain_core.messages import AIMessage

    from engine import ai_provider

    class MockFailingModel:
        async def ainvoke(self, messages):
            raise RuntimeError("Primary provider rate limited (429)")

        def with_fallbacks(self, fallbacks):
            self.fallbacks = fallbacks
            return self

    class MockSuccessfulFallback:
        async def ainvoke(self, messages):
            return AIMessage(content="Fallback response from Groq!")

    # Test automatic fallback execution
    def mock_get_chat_model(provider, *args, **kwargs):
        if provider == "google":
            return MockFailingModel()
        return MockSuccessfulFallback()

    monkeypatch.setattr(ai_provider, "get_chat_model", mock_get_chat_model)

    res = await ai_provider.execute_ai_completion(
        prompt="Analyze Bitcoin",
        provider="google",
        api_key="fake_key",
        fallback_provider="groq",
        fallback_api_key="fake_groq_key"
    )
    assert res == "Fallback response from Groq!"

@pytest.mark.asyncio
async def test_ai_session_manager_and_history():
    from engine.ai_session import SessionManager

    mgr = SessionManager()
    session = mgr.get_or_create_session("test_channel_1")
    session.history.add_user_message("Hello AI!")
    session.history.add_ai_message("Hello human trader.")

    hist = mgr.get_history("test_channel_1")
    assert len(hist) == 2
    assert hist[0]["role"] == "user"
    assert hist[0]["content"] == "Hello AI!"
    assert hist[1]["role"] == "assistant"
    assert hist[1]["content"] == "Hello human trader."

    # Test pruning
    for i in range(25):
        session.history.add_user_message(f"Msg {i}")
    session.prune(max_messages=10)
    assert len(session.history.messages) == 10

    # Test clear
    assert mgr.clear_session("test_channel_1") is True
    assert len(mgr.get_history("test_channel_1")) == 0

@pytest.mark.asyncio
async def test_ai_execute_chat_turn(monkeypatch):
    from langchain_core.messages import AIMessage

    from engine import ai_provider
    from engine.ai_session import execute_chat_turn, session_manager

    session_manager.clear_session("test_chat_session")

    class MockChatModel:
        async def ainvoke(self, messages):
            return AIMessage(content="Bitcoin RSI is consolidating at 45.")

    monkeypatch.setattr(ai_provider, "get_chat_model", lambda *a, **kw: MockChatModel())

    res1 = await execute_chat_turn(
        query="What is BTC RSI right now?",
        session_id="test_chat_session",
        market_context={"prices": {"BTCUSDT": 65000.0}},
        provider="google",
        api_key="fake_key"
    )
    assert "consolidating" in res1["answer"]
    assert res1["turn_count"] == 1

    history = session_manager.get_history("test_chat_session")
    assert len(history) == 2
    assert history[0]["content"] == "What is BTC RSI right now?"
    assert history[1]["content"] == "Bitcoin RSI is consolidating at 45."

    session_manager.clear_session("test_chat_session")

def test_extract_text_from_ai_message_and_multimodal_blocks():
    from langchain_core.messages import AIMessage

    from engine.ai_provider import extract_text_from_ai_message

    # 1. Plain string
    assert extract_text_from_ai_message("Hello BTC") == "Hello BTC"

    # 2. List of dict parts with signature extras (exact Google GenAI SDK artifact)
    raw_blocks = [
        {
            "type": "text",
            "text": "Market Overview & Portfolio Status:\n\n* Market Sentiment: 50/100",
            "extras": {"signature": "El4KXAERTTIP..."}
        }
    ]
    msg = AIMessage(content=raw_blocks)
    cleaned = extract_text_from_ai_message(msg)
    assert cleaned == "Market Overview & Portfolio Status:\n\n* Market Sentiment: 50/100"
    assert "signature" not in cleaned
    assert "extras" not in cleaned

    # 3. Stringified nested python repr (simulating previous turn contamination)
    contaminated = str([{"type": "text", "text": str(raw_blocks), "extras": {"signature": "xyz"}}])
    cleaned_nested = extract_text_from_ai_message(contaminated)
    assert cleaned_nested == "Market Overview & Portfolio Status:\n\n* Market Sentiment: 50/100"

    # 4. Thought blocks filtered out
    mixed = [
        {"type": "thought", "thought": "Thinking about RSI levels..."},
        {"type": "text", "text": "RSI is 45 (Neutral)."}
    ]
    assert extract_text_from_ai_message(mixed) == "RSI is 45 (Neutral)."
