import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("CriptoBotEngine.Risk")

class RiskManager:
    def __init__(self):
        self.max_trade_usdt: float = 50.0       # Max USDT per single trade
        self.max_daily_spend_usdt: float = 200.0 # Max total USDT spent in 24 hours
        self.min_usdt_reserve: float = 20.0     # Keep $20 USDT untouched
        self.require_human_approval: bool = True # Require 3DS / Web confirmation
        self.daily_spent_usdt: float = 0.0

    async def refresh_daily_spend(self, is_testnet: bool = True) -> float:
        try:
            from engine.db import get_rolling_daily_spend
            self.daily_spent_usdt = await get_rolling_daily_spend(is_testnet=is_testnet)
        except Exception as e:
            logger.warning(f"Could not refresh rolling daily spend: {e}")
        return self.daily_spent_usdt

    def validate_trade(self, action: str, amount_usdt: float, current_usdt_balance: float, current_daily_spend: Optional[float] = None) -> Tuple[bool, str]:
        if amount_usdt < 5.0:
            return False, f"Order {amount_usdt:.2f} USDT is below Binance minimum $5.00."

        if action == "BUY":
            if amount_usdt > self.max_trade_usdt:
                return False, f"Trade amount ${amount_usdt:.2f} exceeds max trade limit of ${self.max_trade_usdt:.2f}"
                
            spend_now = current_daily_spend if current_daily_spend is not None else self.daily_spent_usdt
            if (spend_now + amount_usdt) > self.max_daily_spend_usdt:
                return False, f"Trade exceeds rolling 24h spending limit of ${self.max_daily_spend_usdt:.2f} (Current 24h spend: ${spend_now:.2f})"

            if (current_usdt_balance - amount_usdt) < self.min_usdt_reserve:
                return False, f"Trade leaves USDT balance below reserve threshold of ${self.min_usdt_reserve:.2f}"

        return True, "OK"

    def get_max_allowed_buy(self, current_usdt_balance: float) -> float:
        available_balance = current_usdt_balance - self.min_usdt_reserve
        available_daily = self.max_daily_spend_usdt - self.daily_spent_usdt
        return max(0.0, min(self.max_trade_usdt, available_balance, available_daily))

    @property
    def daily_spent(self) -> float:
        return self.daily_spent_usdt

    @daily_spent.setter
    def daily_spent(self, value: float):
        self.daily_spent_usdt = float(value)

    def record_spend(self, amount_usdt: float, action: str = "BUY"):
        if action == "BUY":
            self.daily_spent_usdt += amount_usdt
            logger.info(f"Recorded trade spend: ${amount_usdt:.2f}. Total rolling spend: ${self.daily_spent_usdt:.2f}")

risk_manager = RiskManager()

