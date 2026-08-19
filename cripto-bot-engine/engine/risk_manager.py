import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger("CriptoBotEngine.Risk")

# // ponytail: simple risk rule verification
class RiskManager:
    def __init__(self):
        self.max_trade_usdt: float = 50.0       # Max USDT per single trade
        self.max_daily_spend_usdt: float = 200.0 # Max total USDT spent in 24 hours
        self.min_usdt_reserve: float = 20.0     # Keep $20 USDT untouched
        self.require_human_approval: bool = True # Require 3DS / Web confirmation
        self.daily_spent_usdt: float = 0.0

    def validate_trade(self, action: str, amount_usdt: float, current_usdt_balance: float) -> Tuple[bool, str]:
        if amount_usdt < 5.0:
            return False, f"Order {amount_usdt:.2f} USDT is below Binance minimum $5.00."

        if action == "BUY":
            if amount_usdt > self.max_trade_usdt:
                return False, f"Trade amount ${amount_usdt:.2f} exceeds max trade limit of ${self.max_trade_usdt:.2f}"
                
            if (self.daily_spent_usdt + amount_usdt) > self.max_daily_spend_usdt:
                return False, f"Trade exceeds daily spending limit of ${self.max_daily_spend_usdt:.2f}"

            if (current_usdt_balance - amount_usdt) < self.min_usdt_reserve:
                return False, f"Trade leaves USDT balance below reserve threshold of ${self.min_usdt_reserve:.2f}"

        return True, "OK"

    def get_max_allowed_buy(self, current_usdt_balance: float) -> float:
        available_balance = current_usdt_balance - self.min_usdt_reserve
        available_daily = self.max_daily_spend_usdt - self.daily_spent_usdt
        return max(0.0, min(self.max_trade_usdt, available_balance, available_daily))

    def record_spend(self, amount_usdt: float):
        self.daily_spent_usdt += amount_usdt
        logger.info(f"Recorded trade spend: ${amount_usdt:.2f}. Total today: ${self.daily_spent_usdt:.2f}")

risk_manager = RiskManager()
