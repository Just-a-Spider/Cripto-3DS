import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_fear_and_greed_index():
    from engine.ai_analyst import fetch_fear_and_greed_index
    fng = await fetch_fear_and_greed_index()
    assert isinstance(fng, dict)
    assert "value" in fng
    assert "classification" in fng
    assert 0 <= fng["value"] <= 100

@pytest.mark.asyncio
async def test_chart_generator():
    import io

    from engine.chart_generator import calculate_rsi_series, generate_candlestick_chart

    # Simulated klines
    fake_klines = []
    base_price = 60000.0
    for i in range(30):
        fake_klines.append({
            "time": 1700000000 + i * 3600,
            "open": base_price + i * 10,
            "high": base_price + i * 10 + 50,
            "low": base_price + i * 10 - 30,
            "close": base_price + i * 10 + 20,
            "volume": 100.0
        })

    buf = await generate_candlestick_chart("BTCUSDT", fake_klines, interval="1h")
    assert isinstance(buf, io.BytesIO)
    bytes_data = buf.getvalue()
    assert len(bytes_data) > 100 # Valid PNG image bytes
    assert bytes_data[:8] == b'\x89PNG\r\n\x1a\n' # PNG file signature

    # RSI series calculation test
    closes = [k["close"] for k in fake_klines]
    rsi = calculate_rsi_series(closes, period=14)
    assert len(rsi) == len(closes)
    assert 0.0 <= rsi[-1] <= 100.0

@pytest.mark.asyncio
async def test_news_service_and_confluence():
    import time

    from engine.news_service import NewsItem, news_service
    from engine.strategies import MultiTimeframeFilter

    # 1. Test News Item & Risk Flag
    news_service.cached_news = [
        NewsItem("SEC launches lawsuit against XRP", "XRP", "CryptoPanic", "http://test", "HIGH_RISK", time.time())
    ]
    assert news_service.has_high_risk_event("XRP") is True
    assert news_service.has_high_risk_event("BTC") is False

    # 2. Test MultiTimeframeFilter Confluence
    # Stable trend -> pass
    stable_hist = [100.0, 101.0, 100.5, 102.0, 101.5, 103.0, 102.5, 104.0, 103.5, 105.0]
    ok, reason = MultiTimeframeFilter.evaluate_confluence(stable_hist)
    assert ok is True

    # Severe macro drop (from 100 -> 90 = -10% drop) -> block
    crash_hist = [100.0, 99.0, 98.0, 97.0, 95.0, 94.0, 93.0, 92.0, 91.0, 90.0]
    blocked, block_reason = MultiTimeframeFilter.evaluate_confluence(crash_hist)
    assert blocked is False
    assert "Severe Macro Drop" in block_reason

@pytest.mark.asyncio
async def test_google_search_grounding_provider():
    from engine.news_service import GoogleSearchGroundingProvider
    provider = GoogleSearchGroundingProvider(api_key="")
    items = await provider.fetch_news(["BTC", "ETH"])
    assert len(items) > 0
    assert items[0].asset in ["BTC", "ETH", "MARKET"]

@pytest.mark.asyncio
async def test_news_default_provider_and_grounding_disabled():
    from engine.news_service import CryptoPanicProvider, GoogleSearchGroundingProvider, news_service
    from engine.state import state

    # 1. Default news service uses CryptoPanicProvider
    assert isinstance(news_service.provider, CryptoPanicProvider)

    # 2. Grounding provider respects enable_search_grounding=False
    state.enable_search_grounding = False
    grounding = GoogleSearchGroundingProvider()
    news = await grounding.fetch_news(["BTC"])
    assert isinstance(news, list)
    # Delegates cleanly to CryptoPanic without error
    assert len(news) > 0

@pytest.mark.asyncio
async def test_news_synthesis_fallback():
    import time

    from engine.ai_analyst import fallback_news_synthesis
    from engine.news_service import NewsItem

    sample_items = [
        NewsItem(title="Bitcoin ETF Inflows Surge To Record High", asset="BTC", source="CoinDesk", url="https://example.com", sentiment_tag="BULLISH", published_at=time.time()),
        NewsItem(title="Regulatory Clarity Sparks Altcoin Rally", asset="ETH", source="CoinTelegraph", url="https://example.com", sentiment_tag="BULLISH", published_at=time.time()),
    ]

    res = fallback_news_synthesis(sample_items)
    assert res["overall_catalyst"] == "BULLISH"
    assert len(res["bullets"]) == 3
    assert "BTC" in res["bullets"][0]
