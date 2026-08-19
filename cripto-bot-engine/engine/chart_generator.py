import io
import time
import json
import logging
import aiohttp
from typing import Optional, List, Dict, Any

logger = logging.getLogger("CriptoBotEngine")

async def fetch_klines(pair: str, interval: str = "1h", limit: int = 30) -> Optional[List[dict]]:
    """
    Fetches candlestick klines directly from Binance public API.
    """
    url = f"https://api.binance.com/api/v3/klines?symbol={pair.upper()}&interval={interval}&limit={limit}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to fetch klines for {pair}: HTTP {resp.status}")
                    return None
                data = await resp.json()
                klines = []
                for item in data:
                    klines.append({
                        "time": int(item[0]), # ms
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5])
                    })
                return klines
    except Exception as e:
        logger.warning(f"Error fetching klines for {pair}: {e}")
        return None


def calculate_rsi_series(closes: List[float], period: int = 14) -> List[float]:
    """
    Calculates Wilder's RSI series over a list of close prices.
    """
    if len(closes) < period + 1:
        return [50.0] * len(closes)

    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_series = [50.0] * period
    if avg_loss == 0:
        rsi_series.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period, len(deltas)):
        gain = gains[i]
        loss = losses[i]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_series


async def generate_candlestick_chart(pair: str, klines: List[dict], interval: str = "1h") -> io.BytesIO:
    """
    Generates a dark-theme financial candlestick chart PNG without heavy C++ packages.
    Uses QuickChart v3 engine over async aiohttp. Zero compilation, zero memory overhead.
    """
    candle_data = []
    closes = []
    for k in klines:
        closes.append(k["close"])
        candle_data.append({
            "x": k["time"],
            "o": k["open"],
            "h": k["high"],
            "l": k["low"],
            "c": k["close"]
        })

    last_price = closes[-1] if closes else 0.0
    rsi_vals = calculate_rsi_series(closes, period=14)
    last_rsi = rsi_vals[-1] if rsi_vals else 50.0

    chart_config = {
        "type": "candlestick",
        "data": {
            "datasets": [
                {
                    "label": f"{pair.upper()} ({interval})",
                    "data": candle_data,
                    "color": {
                        "up": "#50fa7b",
                        "down": "#ff5555",
                        "unchanged": "#8be9fd"
                    }
                }
            ]
        },
        "options": {
            "plugins": {
                "title": {
                    "display": True,
                    "text": f"{pair.upper()} • {interval.upper()} | Last: ${last_price:,.4f} | RSI: {last_rsi:.1f}",
                    "color": "#f8f8f2",
                    "font": {"size": 15, "weight": "bold"}
                },
                "legend": {"display": False}
            },
            "scales": {
                "x": {
                    "grid": {"color": "rgba(255, 255, 255, 0.08)"},
                    "ticks": {"color": "#8be9fd"}
                },
                "y": {
                    "grid": {"color": "rgba(255, 255, 255, 0.08)"},
                    "ticks": {"color": "#f8f8f2"}
                }
            }
        }
    }

    payload = {
        "chart": chart_config,
        "version": "3",
        "backgroundColor": "#1e1f29",
        "width": 800,
        "height": 450,
        "devicePixelRatio": 2.0
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://quickchart.io/chart",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10.0)
            ) as resp:
                if resp.status == 200:
                    img_bytes = await resp.read()
                    buf = io.BytesIO(img_bytes)
                    buf.seek(0)
                    return buf
                else:
                    logger.warning(f"QuickChart returned HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"QuickChart request failed: {e}")

    # Fallback to simple PNG generation if QuickChart offline
    return _generate_fallback_image(pair, last_price, last_rsi)


def _generate_fallback_image(pair: str, price: float, rsi: float) -> io.BytesIO:
    """
    Minimal in-memory fallback PNG if external rendering is unreachable.
    """
    # 1x1 transparent PNG fallback buffer
    buf = io.BytesIO(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82')
    buf.seek(0)
    return buf

