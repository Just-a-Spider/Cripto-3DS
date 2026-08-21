import os
import base64
from cryptography.fernet import Fernet
from typing import Dict, List, Any
from binance import AsyncClient
from dotenv import load_dotenv

from engine.logger import logger, recent_logs
from engine.risk_manager import risk_manager
from engine.strategies import DCAStrategy, RSIStrategy, TPSLStrategy, calculate_bollinger_bands

def get_cipher(pin: str) -> Fernet:
    key = base64.urlsafe_b64encode(pin.zfill(32).encode('utf-8'))
    return Fernet(key)

env_file = "testnet.env" if os.path.exists("testnet.env") else "config.env"
load_dotenv(env_file)

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
IS_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

from pydantic import BaseModel

class ConfigModel(BaseModel):
    max_trade_usdt: float
    max_daily_spend_usdt: float
    min_usdt_reserve: float = 20.0
    require_human_approval: bool
    auth_pin: str
    api_key: str = ""
    secret_key: str = ""
    favorite_pairs: str = "BTCUSDT,ETHUSDT"
    testnet: bool = True
    dca_interval: int = 3600
    rsi_threshold: float = 30.0
    tp_percent: float = 5.0
    sl_percent: float = 3.0
    trailing_enabled: bool = True
    trailing_activation_percent: float = 3.0
    trailing_delta_percent: float = 1.5
    partial_tp_enabled: bool = True
    partial_tp_percent: float = 4.0
    partial_tp_ratio: float = 0.5
    bull_regime_dip_enabled: bool = True
    bull_rsi_threshold: float = 42.0
    rsi_timeframe_minutes: int = 60
    rsi_history_length: int = 250
    signal_cooldown_hours: float = 24.0
    discord_webhook_url: str = ""
    discord_bot_token: str = ""
    discord_channel_id: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_search_model: str = "gemini-3.5-flash"
    ai_scout_enabled: bool = True
    ai_scout_interval_hours: float = 2.0
    ai_scout_min_confidence: float = 0.85

class BotState:
    def __init__(self):
        self.is_active: bool = True
        self.testnet: bool = IS_TESTNET
        self.favorite_pairs: List[str] = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
        self.prices: Dict[str, float] = {
            "BTCUSDT": 0.0,
            "ETHUSDT": 0.0,
            "BNBUSDT": 0.0,
            "SOLUSDT": 0.0
        }
        self.usdt_balance: float = 1000.0
        self.portfolio_balances: Dict[str, float] = {}
        self.current_pair_idx: int = 0
        self.pending_trade: Dict[str, Any] = None
        self.binance_client: AsyncClient = None
        self.auth_pin: str = "1234"
        self.api_key: str = ""
        self.secret_key: str = ""
        self.ws_tasks = []
        self.dca_strategy = DCAStrategy()
        self.dca_strategy.enabled = False
        self.signal_cooldown_hours: float = 24.0
        self.rsi_strategy = RSIStrategy()
        self.rsi_strategy.enabled = True
        self.tpsl_strategy = TPSLStrategy()
        self.cost_bases: Dict[str, float] = {}
        self.exchange_filters: Dict[str, Dict[str, Any]] = {}
        self.discord_webhook_url: str = ""
        self.discord_bot_token: str = ""
        self.discord_channel_id: str = ""
        self.gemini_api_key: str = ""
        self.gemini_model: str = "gemini-3.1-flash-lite"
        self.gemini_search_model: str = "gemini-3.5-flash"
        self.available_gemini_models: List[str] = ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.5-flash"]
        self.ai_scout_enabled: bool = True
        self.ai_scout_interval_hours: float = 2.0
        self.ai_scout_min_confidence: float = 0.85

    def sync_favorite_prices(self):
        for pair in self.favorite_pairs:
            if pair not in self.prices:
                self.prices[pair] = 0.0

    def to_dict(self) -> Dict[str, Any]:
        self.sync_favorite_prices()
        indicators = {}
        for pair in self.favorite_pairs:
            rsi_val = round(self.rsi_strategy.calculate_rsi(pair), 1)
            history = self.rsi_strategy.price_histories.get(pair, [])
            _, _, _, pct_b = calculate_bollinger_bands(history, period=20, num_std=2.0)
            indicators[pair] = {
                "rsi": rsi_val,
                "pct_b": round(pct_b, 2)
            }

        return {
            "is_active": self.is_active,
            "testnet": self.testnet,
            "usdt_balance": self.usdt_balance,
            "portfolio": self.portfolio_balances,
            "cost_bases": self.cost_bases,
            "prices": self.prices,
            "indicators": indicators,
            "favorite_pairs": self.favorite_pairs,
            "current_pair": self.favorite_pairs[self.current_pair_idx] if self.favorite_pairs else "BTCUSDT",
            "pending_trade": self.pending_trade,
            "risk_config": {
                "max_trade_usdt": risk_manager.max_trade_usdt,
                "max_daily_spend_usdt": risk_manager.max_daily_spend_usdt,
                "min_usdt_reserve": risk_manager.min_usdt_reserve,
                "require_human_approval": risk_manager.require_human_approval,
                "auth_pin": self.auth_pin
            },
            "has_keys": bool(self.api_key and self.secret_key),
            "strategies": {
                "dca_interval": self.dca_strategy.interval_sec,
                "dca_last_trade": self.dca_strategy.last_trade_time,
                "rsi_threshold": self.rsi_strategy.oversold_rsi,
                "tp_percent": self.tpsl_strategy.tp_percent,
                "sl_percent": self.tpsl_strategy.sl_percent,
                "trailing_enabled": self.tpsl_strategy.trailing_enabled,
                "trailing_activation_percent": self.tpsl_strategy.trailing_activation_percent,
                "trailing_delta_percent": self.tpsl_strategy.trailing_delta_percent,
                "partial_tp_enabled": getattr(self.tpsl_strategy, "partial_tp_enabled", True),
                "partial_tp_percent": getattr(self.tpsl_strategy, "partial_tp_percent", 4.0),
                "partial_tp_ratio": getattr(self.tpsl_strategy, "partial_tp_ratio", 0.5),
                "bull_regime_dip_enabled": getattr(self.rsi_strategy, "bull_regime_dip_enabled", True),
                "bull_rsi_threshold": getattr(self.rsi_strategy, "bull_rsi_threshold", 42.0),
                "rsi_timeframe_minutes": self.rsi_strategy.timeframe_minutes,
                "rsi_history_length": self.rsi_strategy.history_length,
                "signal_cooldown_hours": self.signal_cooldown_hours
            },
            "ai_scout": {
                "enabled": self.ai_scout_enabled,
                "interval_hours": self.ai_scout_interval_hours,
                "min_confidence": self.ai_scout_min_confidence
            },
            "discord_webhook_url": self.discord_webhook_url,
            "has_discord_bot": bool(self.discord_bot_token and self.discord_channel_id),
            "discord_channel_id": self.discord_channel_id,
            "gemini_model": self.gemini_model,
            "gemini_search_model": self.gemini_search_model,
            "has_gemini": bool(self.gemini_api_key),
            "available_gemini_models": self.available_gemini_models,
            "logs": list(recent_logs)
        }

state = BotState()
