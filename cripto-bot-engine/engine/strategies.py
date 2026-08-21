from typing import Dict, Any, List, Optional
import time

class BaseStrategy:
    def __init__(self, name: str):
        self.name = name
        self.enabled = False
        self.cooldowns: Dict[str, float] = {}

    def is_cooling_down(self, pair: str, cooldown_hours: float) -> bool:
        last_time = self.cooldowns.get(pair, 0.0)
        return (time.time() - last_time) < (cooldown_hours * 3600)

    def record_signal(self, pair: str):
        self.cooldowns[pair] = time.time()

    def evaluate(self, prices: Dict[str, float], usdt_balance: float, cooldown_hours: float = 0.0) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

class DCAStrategy(BaseStrategy):
    def __init__(self, interval_sec: int = 3600):
        super().__init__("Dollar Cost Averaging (DCA)")
        self.interval_sec = interval_sec
        self.last_trade_time = time.time()
        self.current_index = 0

    def evaluate(self, prices: Dict[str, float], usdt_balance: float, target_pairs: List[str], cooldown_hours: float = 0.0) -> Optional[Dict[str, Any]]:
        if not self.enabled or not target_pairs:
            return None

        now = time.time()
        if (now - self.last_trade_time) >= self.interval_sec:
            # Round Robin logic
            self.current_index = (self.current_index + 1) % len(target_pairs)
            target = target_pairs[self.current_index]
            
            if self.is_cooling_down(target, cooldown_hours):
                return None
                
            curr_price = prices.get(target, 0.0)
            if curr_price > 0:
                self.last_trade_time = now
                self.record_signal(target)
                return {
                    "strategy": self.name,
                    "action": "BUY",
                    "pair": target,
                    "price": curr_price,
                    "reason": "Dollar Cost Averaging"
                }
        return None

import math
from typing import Dict, Any, List, Optional, Tuple
import time

def calculate_wilder_rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < (period + 1):
        return 50.0

    changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [max(0.0, c) for c in changes[:period]]
    losses = [max(0.0, -c) for c in changes[:period]]
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    for c in changes[period:]:
        gain = max(0.0, c)
        loss = max(0.0, -c)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0) -> Tuple[float, float, float, float]:
    """Returns (middle_sma, upper_band, lower_band, percent_b)."""
    if len(prices) < 5:
        curr = prices[-1] if prices else 0.0
        return curr, curr, curr, 0.5

    eff_period = min(period, len(prices))
    subset = prices[-eff_period:]
    sma = sum(subset) / eff_period
    variance = sum((x - sma) ** 2 for x in subset) / eff_period
    std_dev = math.sqrt(variance)

    upper = sma + (num_std * std_dev)
    lower = sma - (num_std * std_dev)
    band_width = upper - lower
    curr_price = subset[-1]
    
    pct_b = (curr_price - lower) / band_width if band_width > 0 else 0.5
    return sma, upper, lower, pct_b

class MultiTimeframeFilter:
    """
    Evaluates multi-timeframe trend confluence across 15m, 1h, and 4h price histories.
    Prevents buying a short-term oversold dip if macro trend is in a severe crash.
    """
    @staticmethod
    def evaluate_confluence(history: List[float]) -> Tuple[bool, str]:
        if not history or len(history) < 10:
            return True, "OK"

        window = history[-15:] if len(history) >= 15 else history
        peak = max(window)
        curr = window[-1]
        if peak > 0:
            drop_pct = ((peak - curr) / peak) * 100.0
            if drop_pct > 8.0:
                return False, f"Severe Macro Drop (-{drop_pct:.1f}% from peak)"

        return True, "OK"

class RSIStrategy(BaseStrategy):
    def __init__(
        self,
        oversold_rsi: float = 30.0,
        overbought_rsi: float = 70.0,
        timeframe_minutes: int = 60,
        history_length: int = 250,
        min_profit_percent: float = 5.0,
        use_bb_filter: bool = True,
        bull_regime_dip_enabled: bool = True,
        bull_rsi_threshold: float = 42.0
    ):
        super().__init__("RSI Indicator Strategy")
        self.oversold_rsi = oversold_rsi
        self.overbought_rsi = overbought_rsi
        self.timeframe_minutes = timeframe_minutes
        self.history_length = history_length
        self.min_profit_percent = min_profit_percent
        self.use_bb_filter = use_bb_filter
        self.bull_regime_dip_enabled = bull_regime_dip_enabled
        self.bull_rsi_threshold = bull_rsi_threshold
        self.price_histories: Dict[str, List[float]] = {}
        self.last_sample_times: Dict[str, float] = {}

    def add_price(self, pair: str, price: float):
        now = time.time()
        last_time = self.last_sample_times.get(pair, 0.0)
        # Sample the price once every X minutes for indicator trends
        if (now - last_time) >= (self.timeframe_minutes * 60):
            if pair not in self.price_histories:
                self.price_histories[pair] = []
            self.price_histories[pair].append(price)
            self.last_sample_times[pair] = now
            if len(self.price_histories[pair]) > self.history_length:
                self.price_histories[pair].pop(0)

    def calculate_rsi(self, pair: str) -> float:
        history = self.price_histories.get(pair, [])
        return calculate_wilder_rsi(history, period=14)

    def evaluate(self, prices: Dict[str, float], usdt_balance: float, target_pairs: List[str], portfolio: Dict[str, float], cost_bases: Dict[str, float], cooldown_hours: float = 0.0, can_buy: bool = True) -> Optional[Dict[str, Any]]:
        if not self.enabled or not target_pairs:
            return None

        # Sample prices for all pairs
        for pair in target_pairs:
            curr_price = prices.get(pair, 0.0)
            if curr_price > 0:
                self.add_price(pair, curr_price)

        # Dragnet evaluation
        for pair in target_pairs:
            curr_price = prices.get(pair, 0.0)
            if curr_price > 0:
                history = self.price_histories.get(pair, [])
                rsi = self.calculate_rsi(pair)
                sma, upper, lower, pct_b = calculate_bollinger_bands(history, period=20, num_std=2.0)

                # Determine effective oversold threshold:
                # In bull market conditions or lower Bollinger support, allow dynamic dip-buy at <= bull_rsi_threshold
                effective_oversold = self.oversold_rsi
                is_bull_dip = False
                if self.bull_regime_dip_enabled and pct_b <= 0.35:
                    effective_oversold = max(self.oversold_rsi, self.bull_rsi_threshold)
                    if rsi > self.oversold_rsi and rsi <= effective_oversold:
                        is_bull_dip = True

                if rsi <= effective_oversold and can_buy:
                    if not self.is_cooling_down(pair, cooldown_hours):
                        # Multi-timeframe trend confluence check
                        confluence_ok, confluence_reason = MultiTimeframeFilter.evaluate_confluence(history)
                        if not confluence_ok:
                            continue

                        # Falling knife check: if %b is negative and 3 consecutive steep falling candles, wait for stabilization
                        is_falling_knife = False
                        if self.use_bb_filter and len(history) >= 4:
                            if pct_b < 0.0 and (history[-1] < history[-2] < history[-3]):
                                is_falling_knife = True

                        if not is_falling_knife:
                            self.record_signal(pair)
                            reason_str = (
                                f"Bull Regime Dip Buy (RSI: {rsi:.1f} <= {effective_oversold:.1f}, %B: {pct_b:.2f})"
                                if is_bull_dip
                                else f"RSI Oversold ({rsi:.1f}) + BB %B ({pct_b:.2f})"
                            )
                            return {
                                "strategy": self.name,
                                "action": "BUY",
                                "pair": pair,
                                "price": curr_price,
                                "rsi": rsi,
                                "pct_b": pct_b,
                                "reason": reason_str
                            }
                elif rsi >= self.overbought_rsi:
                    asset = pair.replace("USDT", "")
                    qty = portfolio.get(asset, 0.0)
                    avg_price = cost_bases.get(pair, 0.0)
                    if qty > 0 and (qty * curr_price) >= 5.0 and avg_price > 0:
                        profit_pct = ((curr_price - avg_price) / avg_price) * 100.0
                        if profit_pct >= self.min_profit_percent:
                            # Trailing Profit Runner: If trailing mode is active, arm the peak and ride the pump
                            from engine.state import state
                            if getattr(state, "tpsl_strategy", None) and state.tpsl_strategy.trailing_enabled:
                                prev_peak = state.tpsl_strategy.peak_prices.get(pair, avg_price)
                                state.tpsl_strategy.peak_prices[pair] = max(prev_peak, curr_price)
                                # Let TPSL trailing runner ride the trend instead of static dumping
                            else:
                                self.record_signal(pair)
                                return {
                                    "strategy": self.name,
                                    "action": "SELL",
                                    "pair": pair,
                                    "amount_asset": qty,
                                    "price": curr_price,
                                    "rsi": rsi,
                                    "pct_b": pct_b,
                                    "reason": f"RSI Overbought ({rsi:.1f}) + BB %B ({pct_b:.2f}) + Profit ({profit_pct:.1f}%)"
                                }
        return None

class TPSLStrategy(BaseStrategy):
    def __init__(
        self,
        tp_percent: float = 5.0,
        sl_percent: float = 3.0,
        trailing_enabled: bool = True,
        trailing_activation_percent: float = 3.0,
        trailing_delta_percent: float = 1.5,
        partial_tp_enabled: bool = False,
        partial_tp_percent: float = 4.0,
        partial_tp_ratio: float = 0.5
    ):
        super().__init__("Take Profit / Stop Loss")
        self.tp_percent = tp_percent
        self.sl_percent = sl_percent
        self.trailing_enabled = trailing_enabled
        self.trailing_activation_percent = trailing_activation_percent
        self.trailing_delta_percent = trailing_delta_percent
        self.partial_tp_enabled = partial_tp_enabled
        self.partial_tp_percent = partial_tp_percent
        self.partial_tp_ratio = partial_tp_ratio
        self.peak_prices: Dict[str, float] = {}
        self.custom_trail_deltas: Dict[str, float] = {}
        self.tp_staged_positions: Dict[str, float] = {}
        self.enabled = True

    def evaluate_tpsl(self, prices: Dict[str, float], portfolio: Dict[str, float], cost_bases: Dict[str, float], cooldown_hours: float = 0.0) -> Optional[Dict[str, Any]]:
        if not self.enabled: return None
        
        for asset, qty in portfolio.items():
            if asset == "USDT" or qty <= 0.0:
                continue
                
            pair = asset + "USDT"
            curr_price = prices.get(pair, 0.0)
            avg_price = cost_bases.get(pair, 0.0)
            
            if curr_price > 0 and avg_price > 0:
                if (qty * curr_price) < 5.0:
                    continue
                
                profit_pct = ((curr_price - avg_price) / avg_price) * 100.0

                # 1. Staged Partial Take-Profit Check (TP1 Scale-Out)
                if self.partial_tp_enabled and pair not in self.tp_staged_positions and profit_pct >= self.partial_tp_percent:
                    partial_qty = qty * self.partial_tp_ratio
                    if (partial_qty * curr_price) >= 5.0 and not self.is_cooling_down(pair, cooldown_hours):
                        self.tp_staged_positions[pair] = time.time()
                        self.record_signal(pair)
                        return {
                            "strategy": self.name,
                            "action": "SELL",
                            "pair": pair,
                            "amount_asset": partial_qty,
                            "price": curr_price,
                            "is_partial_tp": True,
                            "partial_ratio": self.partial_tp_ratio,
                            "reason": f"Partial Take Profit (TP1 +{profit_pct:.1f}% on {int(self.partial_tp_ratio * 100)}% position)"
                        }

                # 2. Breakeven Stop Check for Staged Positions (Protect secured gains)
                if pair in self.tp_staged_positions and profit_pct <= 0.15:
                    if not self.is_cooling_down(pair, cooldown_hours):
                        self.record_signal(pair)
                        self.tp_staged_positions.pop(pair, None)
                        self.peak_prices.pop(pair, None)
                        self.custom_trail_deltas.pop(pair, None)
                        return {
                            "strategy": self.name,
                            "action": "SELL",
                            "pair": pair,
                            "amount_asset": qty,
                            "price": curr_price,
                            "reason": f"Breakeven Stop Protection (+{profit_pct:.2f}%)"
                        }
                
                if self.trailing_enabled:
                    # Check if profit reached activation threshold
                    if profit_pct >= self.trailing_activation_percent:
                        prev_peak = self.peak_prices.get(pair, avg_price)
                        new_peak = max(prev_peak, curr_price)
                        self.peak_prices[pair] = new_peak
                        
                        effective_delta = self.custom_trail_deltas.get(pair, self.trailing_delta_percent)
                        pullback_pct = ((new_peak - curr_price) / new_peak) * 100.0
                        if pullback_pct >= effective_delta:
                            if not self.is_cooling_down(pair, cooldown_hours):
                                self.record_signal(pair)
                                self.peak_prices.pop(pair, None)
                                self.custom_trail_deltas.pop(pair, None)
                                self.tp_staged_positions.pop(pair, None)
                                return {
                                    "strategy": self.name,
                                    "action": "SELL",
                                    "pair": pair,
                                    "amount_asset": qty,
                                    "price": curr_price,
                                    "reason": f"Trailing Stop (Peak: ${new_peak:.2f}, Profit: +{profit_pct:.2f}%)"
                                }
                    # Hard Stop Loss check
                    if profit_pct <= -self.sl_percent:
                        if not self.is_cooling_down(pair, cooldown_hours):
                            self.record_signal(pair)
                            self.peak_prices.pop(pair, None)
                            self.custom_trail_deltas.pop(pair, None)
                            self.tp_staged_positions.pop(pair, None)
                            return {
                                "strategy": self.name,
                                "action": "SELL",
                                "pair": pair,
                                "amount_asset": qty,
                                "price": curr_price,
                                "reason": f"Stop Loss ({profit_pct:.2f}%)"
                            }
                else:
                    if profit_pct >= self.tp_percent:
                        if not self.is_cooling_down(pair, cooldown_hours):
                            self.record_signal(pair)
                            self.tp_staged_positions.pop(pair, None)
                            return {
                                "strategy": self.name,
                                "action": "SELL",
                                "pair": pair,
                                "amount_asset": qty,
                                "price": curr_price,
                                "reason": f"Take Profit (+{profit_pct:.2f}%)"
                            }
                    elif profit_pct <= -self.sl_percent:
                        if not self.is_cooling_down(pair, cooldown_hours):
                            self.record_signal(pair)
                            self.tp_staged_positions.pop(pair, None)
                            return {
                                "strategy": self.name,
                                "action": "SELL",
                                "pair": pair,
                                "amount_asset": qty,
                                "price": curr_price,
                                "reason": f"Stop Loss ({profit_pct:.2f}%)"
                            }
        return None
