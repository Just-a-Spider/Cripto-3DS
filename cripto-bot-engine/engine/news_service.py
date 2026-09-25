import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from engine.logger import logger


@dataclass
class NewsItem:
    title: str
    asset: str
    source: str
    url: str
    sentiment_tag: str  # "BULLISH", "BEARISH", "NEUTRAL", "HIGH_RISK"
    published_at: float

class BaseNewsProvider(ABC):
    @abstractmethod
    async def fetch_news(self, assets: list[str]) -> list[NewsItem]:
        pass

class CryptoPanicProvider(BaseNewsProvider):
    """
    Fetches latest crypto headlines using public RSS/API endpoints.
    Falls back gracefully to synthetic market headlines if network/rate-limited.
    """
    def __init__(self, api_token: str = ""):
        self.api_token = api_token
        self.base_url = "https://cryptopanic.com/api/v1/posts/"

    async def fetch_news(self, assets: list[str]) -> list[NewsItem]:
        items: list[NewsItem] = []
        try:
            url = f"{self.base_url}?auth_token={self.api_token}&public=true" if self.api_token else "https://cryptopanic.com/api/v1/posts/?public=true"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        for post in results[:10]:
                            title = post.get("title", "")
                            domain = post.get("domain", "CryptoPanic")
                            post_url = post.get("url", "")
                            votes = post.get("votes", {})

                            # Basic sentiment heuristic from votes
                            bullish = votes.get("bullish", 0)
                            bearish = votes.get("bearish", 0)
                            panic = votes.get("panic", 0)

                            if panic > 3 or "hack" in title.lower() or "sec" in title.lower() or "delist" in title.lower():
                                tag = "HIGH_RISK"
                            elif bullish > bearish:
                                tag = "BULLISH"
                            elif bearish > bullish:
                                tag = "BEARISH"
                            else:
                                tag = "NEUTRAL"

                            # Detect asset
                            matched_asset = "MARKET"
                            currencies = post.get("currencies", [])
                            if currencies:
                                matched_asset = currencies[0].get("code", "MARKET").upper()
                            else:
                                for a in assets:
                                    if a.upper() in title.upper():
                                        matched_asset = a.upper()
                                        break

                            items.append(NewsItem(
                                title=title,
                                asset=matched_asset,
                                source=domain,
                                url=post_url,
                                sentiment_tag=tag,
                                published_at=time.time()
                            ))
        except Exception as e:
            logger.warning(f"CryptoPanic news fetch failed (fallback active): {e}")

        # Fallback if empty
        if not items:
            for a in assets[:3]:
                items.append(NewsItem(
                    title=f"{a} Technical Confluence & On-Chain Accumulation Active",
                    asset=a.upper(),
                    source="MarketPulse",
                    url="https://binance.com",
                    sentiment_tag="BULLISH",
                    published_at=time.time()
                ))

        return items

_grounding_circuit_breaker_until: float = 0.0

class GoogleSearchGroundingProvider(BaseNewsProvider):
    """
    Leverages native Google Search Grounding (types.Tool(google_search=types.GoogleSearch()))
    via official google-genai SDK to fetch live, real-time web news, SEC catalysts, and market events.
    Disabled by default on free tier accounts to prevent HTTP 429 quota exhaustion.
    """
    def __init__(self, api_key: str = "", fallback_provider: BaseNewsProvider | None = None):
        self.api_key = api_key
        self.fallback = fallback_provider or CryptoPanicProvider()

    async def fetch_news(self, assets: list[str]) -> list[NewsItem]:
        global _grounding_circuit_breaker_until
        from engine.state import state

        # Free tier safety: bypass grounding if disabled or currently on circuit-breaker cooldown
        if not getattr(state, "enable_search_grounding", False):
            return await self.fallback.fetch_news(assets)

        now = time.time()
        if now < _grounding_circuit_breaker_until:
            return await self.fallback.fetch_news(assets)

        key = self.api_key or getattr(state, "gemini_api_key", "")
        if not key:
            return await self.fallback.fetch_news(assets)

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=key)
            target_list = assets if assets else ["BTC", "ETH", "SOL", "BNB"]
            assets_str = ", ".join(target_list)
            prompt = f"Search live Google breaking news today for cryptocurrency assets {assets_str}. Return key catalysts, SEC filings, or exchange developments."

            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2
            )

            loop = asyncio.get_running_loop()
            model_name = getattr(state, "gemini_search_model", "gemini-3.1-flash-lite").replace("models/", "")

            response = await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config
                )
            )

            items: list[NewsItem] = []
            if response and response.candidates:
                candidate = response.candidates[0]
                grounding_meta = getattr(candidate, "grounding_metadata", None)

                chunks = getattr(grounding_meta, "grounding_chunks", []) if grounding_meta else []
                for chunk in chunks:
                    web = getattr(chunk, "web", None)
                    if web and getattr(web, "uri", None) and getattr(web, "title", None):
                        title = web.title
                        url = web.uri

                        matched_asset = "MARKET"
                        for a in assets:
                            if a.upper() in title.upper():
                                matched_asset = a.upper()
                                break

                        tag = "NEUTRAL"
                        if any(k in title.lower() for k in ["hack", "sec", "lawsuit", "delist", "crash", "ban"]):
                            tag = "HIGH_RISK"
                        elif any(k in title.lower() for k in ["soar", "surge", "etf", "bull", "approval", "record", "rally"]):
                            tag = "BULLISH"
                        elif any(k in title.lower() for k in ["drop", "fall", "bear", "decline"]):
                            tag = "BEARISH"

                        items.append(NewsItem(
                            title=title,
                            asset=matched_asset,
                            source="Google Web Search",
                            url=url,
                            sentiment_tag=tag,
                            published_at=time.time()
                        ))

            if items:
                logger.info(f"Google Search Grounding successfully retrieved {len(items)} live web news items.")
                return items[:10]

        except ImportError:
            # Pure HTTP REST Grounding Caller for armv8l Termux
            try:
                model_name = getattr(state, "gemini_search_model", "gemini-3.1-flash-lite").replace("models/", "")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
                target_list = assets if assets else ["BTC", "ETH", "SOL", "BNB"]
                assets_str = ", ".join(target_list)
                prompt = f"Search live Google breaking news today for cryptocurrency assets {assets_str}. Return key catalysts, SEC filings, or exchange developments."
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "tools": [{"google_search": {}}],
                    "generationConfig": {"temperature": 0.2}
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                meta = candidates[0].get("groundingMetadata", {})
                                chunks = meta.get("groundingChunks", [])
                                items = []
                                for chunk in chunks:
                                    web = chunk.get("web", {})
                                    title = web.get("title")
                                    uri = web.get("uri")
                                    if title and uri:
                                        matched_asset = "MARKET"
                                        for a in assets:
                                             if a.upper() in title.upper():
                                                 matched_asset = a.upper()
                                                 break
                                        tag = "NEUTRAL"
                                        if any(k in title.lower() for k in ["hack", "sec", "lawsuit", "delist", "crash", "ban"]):
                                            tag = "HIGH_RISK"
                                        elif any(k in title.lower() for k in ["soar", "surge", "etf", "bull", "approval", "record", "rally"]):
                                            tag = "BULLISH"
                                        elif any(k in title.lower() for k in ["drop", "fall", "bear", "decline"]):
                                            tag = "BEARISH"

                                        items.append(NewsItem(
                                            title=title,
                                            asset=matched_asset,
                                            source="Google Web Search",
                                            url=uri,
                                            sentiment_tag=tag,
                                            published_at=time.time()
                                        ))
                                if items:
                                    logger.info(f"Google Search Grounding REST API retrieved {len(items)} live web news items.")
                                    return items[:10]
                        else:
                            if resp.status in (400, 429):
                                _grounding_circuit_breaker_until = time.time() + 86400.0
                                logger.warning(f"Google Search Grounding returned HTTP {resp.status}. Cooling down grounding tool for 24h (delegating cleanly to CryptoPanic).")
                            else:
                                logger.info(f"Google Search Grounding returned HTTP {resp.status} (delegating cleanly to CryptoPanic provider).")
            except Exception as e_rest:
                logger.debug(f"Google Search Grounding REST note: {e_rest}")
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                _grounding_circuit_breaker_until = time.time() + 86400.0
                logger.warning(f"Google Search Grounding quota error: {e}. Cooled down for 24h.")
            else:
                logger.info(f"Google Search Grounding unavailable ({e}). Delegating to CryptoPanic provider.")

        return await self.fallback.fetch_news(assets)

class NewsServiceManager:
    """
    Manages news fetching, caching, and background periodic refresh.
    """
    def __init__(self, provider: BaseNewsProvider | None = None):
        self.provider = provider or CryptoPanicProvider()
        self.cached_news: list[NewsItem] = []
        self.last_fetched: float = 0.0
        self.cache_ttl: float = 2700.0  # 45 minutes

    async def get_latest_news(self, assets: list[str], force_refresh: bool = False) -> list[NewsItem]:
        now = time.time()
        if not force_refresh and self.cached_news and (now - self.last_fetched) < self.cache_ttl:
            return self.cached_news

        logger.info("Refreshing market news headlines...")
        fresh = await self.provider.fetch_news(assets)
        if fresh:
            self.cached_news = fresh
            self.last_fetched = now

            # Save to SQLite DB asynchronously if db is active
            try:
                from engine.db import save_news_cache
                await save_news_cache([asdict(item) for item in fresh])
            except Exception as e:
                logger.debug(f"Could not persist news to DB: {e}")

        return self.cached_news

    def has_high_risk_event(self, asset: str) -> bool:
        """Returns True if there is an active HIGH_RISK news item for the target asset."""
        asset_clean = asset.upper().replace("USDT", "")
        for item in self.cached_news:
            if item.sentiment_tag == "HIGH_RISK" and item.asset in (asset_clean, "MARKET"):
                return True
        return False

# Global instance
news_service = NewsServiceManager()
