import json
import logging
from typing import Any, Dict, List, Optional

from engine.ai_analyst import DEFAULT_GEMINI_MODEL, call_ai, fetch_fear_and_greed_index
from engine.logger import logger


def calculate_dynamic_trade_parameters(
    price: Any = 0.0,
    rsi: Any = 50.0,
    pct_b: float = 0.5,
    price_history: list[float] | None = None,
    consecutive_losses: int = 0,
    win_rate: float = 50.0,
    **kwargs
) -> dict[str, float]:
    """
    Computes volatility-adaptive Take Profit and Stop Loss percentages.
    Adapts based on recent price spread, %B deviation, and historical win rate.
    Supports both direct metric calls and asset track-record context calls.
    """
    if isinstance(price, str):
        # Polymorphic invocation: (pair, asset_history, ...)
        if isinstance(rsi, dict):
            asset_history = rsi
            consecutive_losses = int(asset_history.get("consecutive_losses", consecutive_losses))
            win_rate = float(asset_history.get("win_rate", win_rate))
        price = float(kwargs.get("price", 0.0))
        rsi = float(kwargs.get("rsi", 50.0))
    else:
        try:
            price = float(price)
            rsi = float(rsi)
        except (ValueError, TypeError):
            price = 0.0
            rsi = 50.0

    # Baseline defaults
    base_tp = 5.0
    base_sl = 3.0

    # Volatility estimation from price history if available
    vol_spread_pct = 2.5
    if price_history and len(price_history) >= 10:
        recent = price_history[-10:]
        high_p = max(recent)
        low_p = min(recent)
        if low_p > 0:
            vol_spread_pct = max(1.0, min(10.0, ((high_p - low_p) / low_p) * 100.0))

    # Adjust TP based on volatility spread
    if vol_spread_pct > 5.0:
        # High volatility market -> widen TP target
        dynamic_tp = round(min(8.0, base_tp + (vol_spread_pct * 0.3)), 1)
        dynamic_sl = round(min(4.5, base_sl + (vol_spread_pct * 0.15)), 1)
    elif vol_spread_pct < 1.8:
        # Low volatility / compression -> tighter scalping TP
        dynamic_tp = round(max(3.0, base_tp - 1.5), 1)
        dynamic_sl = round(max(1.8, base_sl - 0.8), 1)
    else:
        dynamic_tp = base_tp
        dynamic_sl = base_sl

    # Safety Guardrail: If asset is on a loss streak, enforce strict stop loss
    if consecutive_losses >= 3:
        dynamic_sl = min(dynamic_sl, 2.0)
    elif consecutive_losses == 2:
        dynamic_sl = min(dynamic_sl, 2.5)

    # If asset has high historical win rate (>70%), allow standard runner TP
    if win_rate >= 75.0 and consecutive_losses == 0:
        dynamic_tp = max(dynamic_tp, 5.5)

    return {
        "dynamic_tp_percent": dynamic_tp,
        "dynamic_sl_percent": dynamic_sl,
        "volatility_spread_percent": round(vol_spread_pct, 2)
    }

def project_trade_outcome(
    trade: dict[str, Any],
    cost_basis: float = 0.0,
    current_holdings: float = 0.0,
    dynamic_tp: float = 5.0,
    dynamic_sl: float = 3.0
) -> dict[str, Any]:
    """
    Deterministically computes exact expected financial returns and risk metrics
    prior to order execution.
    Eliminates AI math errors and provides exact dollar projections for UI and prompts.
    """
    action = str(trade.get("action", "BUY")).upper()
    price = float(trade.get("price", 0.0))
    amount_usdt = float(trade.get("amount_usdt", 0.0))
    amount_asset = float(trade.get("amount_asset", 0.0))

    if price > 0.0 and amount_asset <= 0.0 and amount_usdt > 0.0:
        amount_asset = amount_usdt / price
    elif price > 0.0 and amount_usdt <= 0.0 and amount_asset > 0.0:
        amount_usdt = amount_asset * price

    if action == "SELL":
        sold_qty = amount_asset
        if cost_basis > 0.0 and price > 0.0 and sold_qty > 0.0:
            cost_total = cost_basis * sold_qty
            projected_pnl_usdt = round((price - cost_basis) * sold_qty, 2)
            projected_pnl_percent = round(((price - cost_basis) / cost_basis) * 100.0, 2)
        else:
            cost_total = 0.0
            projected_pnl_usdt = 0.0
            projected_pnl_percent = 0.0

        if projected_pnl_usdt > 0.01:
            outcome_type = "PROFIT"
            summary_str = f"Locks in +${projected_pnl_usdt:.2f} USDT (+{projected_pnl_percent:.1f}%)"
        elif projected_pnl_usdt < -0.01:
            outcome_type = "LOSS"
            summary_str = f"Realizes -${abs(projected_pnl_usdt):.2f} USDT ({projected_pnl_percent:.1f}%)"
        else:
            outcome_type = "BREAKEVEN"
            summary_str = "Breakeven exit ($0.00 PnL)"

        return {
            "action": "SELL",
            "price": price,
            "cost_basis": cost_basis,
            "sell_qty": round(sold_qty, 6),
            "cost_total_usdt": round(cost_total, 2),
            "projected_pnl_usdt": projected_pnl_usdt,
            "projected_pnl_percent": projected_pnl_percent,
            "outcome_type": outcome_type,
            "summary_str": summary_str
        }

    else:  # BUY
        target_profit_usdt = round(amount_usdt * (dynamic_tp / 100.0), 2)
        max_risk_usdt = round(amount_usdt * (dynamic_sl / 100.0), 2)
        target_tp_price = round(price * (1.0 + dynamic_tp / 100.0), 4)
        stop_loss_price = round(price * (1.0 - dynamic_sl / 100.0), 4)
        rr_ratio = round(target_profit_usdt / max_risk_usdt, 2) if max_risk_usdt > 0.0 else 1.0

        # Calculate blended entry price if already holding position
        new_qty = amount_asset
        if current_holdings > 0.0 and cost_basis > 0.0:
            total_held_cost = current_holdings * cost_basis
            new_total_cost = total_held_cost + amount_usdt
            new_total_qty = current_holdings + new_qty
            blended_entry = round(new_total_cost / new_total_qty, 4) if new_total_qty > 0.0 else price
        else:
            blended_entry = price

        summary_str = f"Target +${target_profit_usdt:.2f} (+{dynamic_tp:.1f}%) | Risk -${max_risk_usdt:.2f} (-{dynamic_sl:.1f}%) | R:R {rr_ratio}:1"

        return {
            "action": "BUY",
            "price": price,
            "amount_usdt": round(amount_usdt, 2),
            "amount_asset": round(new_qty, 6),
            "target_tp_price": target_tp_price,
            "stop_loss_price": stop_loss_price,
            "target_profit_usdt": target_profit_usdt,
            "max_risk_usdt": max_risk_usdt,
            "risk_reward_ratio": rr_ratio,
            "dynamic_tp_percent": dynamic_tp,
            "dynamic_sl_percent": dynamic_sl,
            "blended_cost_basis": blended_entry,
            "summary_str": summary_str
        }

def fallback_agentic_decision(
    pair: str,
    action: str,
    price: float,
    rsi: float,
    pct_b: float,
    reason: str,
    max_trade_usdt: float,
    asset_history: dict[str, Any] | None = None,
    fng_val: int = 50,
    fng_class: str = "Neutral",
    dyn_params: dict[str, float] | None = None
) -> dict[str, Any]:
    """
    Tier 0 Deterministic Mathematical Agentic Decision Engine.
    Ensures safe, bounded trading decisions even during LLM outages.
    """
    dyn = dyn_params or calculate_dynamic_trade_parameters(price, rsi, pct_b)
    hist = asset_history or {}
    closed = hist.get("closed_trades", 0)
    win_rate = hist.get("win_rate", 50.0)
    consec_losses = hist.get("consecutive_losses", 0)
    pnl = hist.get("net_realized_pnl_usdt", 0.0)

    guardrails = []
    adjusted_size = max_trade_usdt

    # Loss streak circuit breaker
    if consec_losses >= 3:
        guardrails.append("LOSS_STREAK_THROTTLED: 3+ consecutive losses - size reduced 50%")
        adjusted_size = round(max_trade_usdt * 0.5, 2)
    elif consec_losses == 2:
        guardrails.append("LOSS_CAUTION: 2 consecutive losses - cautious size")
        adjusted_size = round(max_trade_usdt * 0.75, 2)

    # Extreme macro sentiment guardrails
    if fng_val >= 80:
        guardrails.append("EXTREME_GREED: Market euphoric - risk of sharp pullback")
    elif fng_val <= 20:
        guardrails.append("EXTREME_FEAR: High market panic - monitor for capitulation")

    # Breaking news emergency check
    from engine.news_service import news_service
    if news_service.has_high_risk_event(pair):
        guardrails.append("BREAKING_NEWS_CATALYST: Active high-risk headline detected")
        return {
            "verdict": "HIGH_RISK",
            "risk_score": 9,
            "confidence": 0.95,
            "adjusted_position_size_usdt": round(max_trade_usdt * 0.25, 2),
            "suggested_tp_percent": dyn["dynamic_tp_percent"],
            "suggested_sl_percent": 2.0,
            "historical_factor": f"Track Record: {closed} trades, {win_rate}% Win Rate, ${pnl:+.2f} PnL",
            "guardrail_flags": guardrails,
            "summary": f"[AGENTIC RISK] Emergency news catalyst active for {pair}. Blocked or heavily reduced."
        }

    # Core scoring logic
    risk_score = 4
    if action == "BUY":
        if rsi <= 30.0 and pct_b <= 0.20:
            risk_score = 2 if consec_losses == 0 else 4
            verdict = "APPROVE"
        elif rsi <= 42.0 and pct_b <= 0.35:
            risk_score = 3 if consec_losses == 0 else 5
            verdict = "APPROVE"
        elif rsi >= 65.0:
            risk_score = 7
            verdict = "CAUTION"
            guardrails.append("OVERBOUGHT: RSI overextended for BUY signal")
        else:
            verdict = "APPROVE" if consec_losses < 3 else "CAUTION"
            risk_score = 5
    else:  # SELL
        if rsi >= 65.0:
            risk_score = 2
            verdict = "APPROVE"
        elif rsi <= 35.0:
            risk_score = 6
            verdict = "CAUTION"
            guardrails.append("OVERSOLD_SELL: Selling near support")
        else:
            risk_score = 4
            verdict = "APPROVE"

    if consec_losses >= 3 and verdict == "APPROVE":
        verdict = "CAUTION"
        risk_score = max(risk_score, 6)

    confidence = 0.88 if closed >= 5 else 0.80

    hist_desc = f"Historical Track Record: {closed} closed trades, {win_rate}% WR, ${pnl:+.2f} PnL, {consec_losses} loss streak"
    summary = (
        f"[ALGO AGENTIC] Technical confluence RSI {rsi:.1f}, %B {pct_b:.2f}, F&G {fng_val}/100. "
        f"{hist_desc}. Size adjusted to ${adjusted_size:.2f}."
    )

    proj = project_trade_outcome(
        trade={"action": action, "price": price, "amount_usdt": adjusted_size},
        cost_basis=float(hist.get("current_cost_basis", 0.0)),
        current_holdings=float(hist.get("current_position_qty", 0.0)),
        dynamic_tp=dyn["dynamic_tp_percent"],
        dynamic_sl=dyn["dynamic_sl_percent"]
    )

    return {
        "verdict": verdict,
        "risk_score": risk_score,
        "confidence": confidence,
        "adjusted_position_size_usdt": adjusted_size,
        "suggested_tp_percent": dyn["dynamic_tp_percent"],
        "suggested_sl_percent": dyn["dynamic_sl_percent"],
        "historical_factor": hist_desc,
        "guardrail_flags": guardrails,
        "summary": summary,
        "projection": proj
    }

async def evaluate_agentic_trade_decision(
    pair: str,
    action: str,
    price: float,
    rsi: float,
    pct_b: float,
    reason: str,
    max_trade_usdt: float = 20.0,
    price_history: list[float] | None = None,
    asset_history: dict[str, Any] | None = None,
    api_key: str = "",
    model: str = DEFAULT_GEMINI_MODEL
) -> dict[str, Any]:
    """
    Evaluates a trading signal through an Agentic Decision Pipeline:
    1. Historical Memory & Asset Performance Grounding (win rates, loss streaks, PnL)
    2. Volatility-Adaptive Parameter Derivation (dynamic TP/SL)
    3. Safety Circuit Breakers & Position Throttling
    4. Multi-Tier AI Reasoning (Primary LLM -> Groq -> Deterministic Math)
    """
    fng = await fetch_fear_and_greed_index()
    fng_val = int(fng.get("value", 50))
    fng_class = str(fng.get("classification", "Neutral"))

    hist = asset_history or {}
    closed = int(hist.get("closed_trades", 0))
    win_rate = float(hist.get("win_rate", 50.0))
    consec_losses = int(hist.get("consecutive_losses", 0))
    net_pnl = float(hist.get("net_realized_pnl_usdt", 0.0))
    status = str(hist.get("performance_status", "NEUTRAL"))

    dyn = calculate_dynamic_trade_parameters(
        price=price,
        rsi=rsi,
        pct_b=pct_b,
        price_history=price_history,
        consecutive_losses=consec_losses,
        win_rate=win_rate
    )

    if not api_key:
        return fallback_agentic_decision(
            pair=pair, action=action, price=price, rsi=rsi, pct_b=pct_b,
            reason=reason, max_trade_usdt=max_trade_usdt, asset_history=hist,
            fng_val=fng_val, fng_class=fng_class, dyn_params=dyn
        )

    # Build prompt with historical performance grounding
    hist_grounding = f"""
Asset Historical Performance Grounding for {pair}:
- Total Closed Trades: {closed}
- Historical Win Rate: {win_rate}%
- Net Realized PnL: ${net_pnl:+.2f} USDT
- Current Consecutive Loss Streak: {consec_losses}
- Asset Performance Status: {status}
"""

    proj = project_trade_outcome(
        trade={"action": action, "price": price, "amount_usdt": max_trade_usdt},
        cost_basis=float(hist.get("current_cost_basis", 0.0)),
        current_holdings=float(hist.get("current_position_qty", 0.0)),
        dynamic_tp=dyn["dynamic_tp_percent"],
        dynamic_sl=dyn["dynamic_sl_percent"]
    )

    prompt = f"""
Evaluate this cryptocurrency trade decision using quantitative reasoning and agentic risk guardrails:
- Pair: {pair}
- Action: {action}
- Current Price: ${price:,.4f}
- 14-period Wilder RSI: {rsi:.1f}
- Bollinger Band %B: {pct_b:.2f}
- Signal Reason: {reason}
- Macro Market Sentiment: Fear & Greed Index {fng_val}/100 ({fng_class})
- Base Trade Size: ${max_trade_usdt:.2f} USDT
- Suggested Dynamic TP/SL: +{dyn['dynamic_tp_percent']}% / -{dyn['dynamic_sl_percent']}%
- Pre-Trade Mathematical Projection: {proj['summary_str']}
{hist_grounding}

Safety Guardrail Rules to Enforce:
1. If consecutive losses >= 3, reduce position size by 50% and do NOT approve with confidence under 0.90.
2. If asset has >75% win rate and 0 loss streak, maintain standard size.
3. If extreme greed (>80) on BUY, flag caution.
4. Output concise, objective quantitative rationale under 45 words.

Return strictly valid JSON with exact format:
{{
  "verdict": "APPROVE" | "CAUTION" | "HIGH_RISK",
  "risk_score": integer 1 to 10 (1=safest, 10=dangerous),
  "confidence": float 0.0 to 1.0,
  "adjusted_position_size_usdt": float (e.g. {max_trade_usdt:.2f} or reduced),
  "suggested_tp_percent": float,
  "suggested_sl_percent": float,
  "historical_factor": "1 concise sentence evaluating how past performance influenced decision",
  "guardrail_flags": ["list", "of", "active", "safety", "flags"],
  "summary": "1-2 sentences concise evaluation"
}}
"""
    system_inst = "You are an autonomous quantitative crypto risk officer. Apply rigorous safety guardrails and return strictly valid JSON."

    raw = await call_ai(prompt, system_instruction=system_inst, api_key=api_key, model=model, json_mode=True)
    if not raw:
        return fallback_agentic_decision(
            pair=pair, action=action, price=price, rsi=rsi, pct_b=pct_b,
            reason=reason, max_trade_usdt=max_trade_usdt, asset_history=hist,
            fng_val=fng_val, fng_class=fng_class, dyn_params=dyn
        )

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        data = json.loads(cleaned)

        verdict = str(data.get("verdict", "CAUTION")).upper()
        if verdict not in ["APPROVE", "CAUTION", "HIGH_RISK"]:
            verdict = "CAUTION"

        risk_score = max(1, min(10, int(data.get("risk_score", 5))))
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.8))))

        adj_size = float(data.get("adjusted_position_size_usdt", max_trade_usdt))
        adj_size = max(5.0, min(max_trade_usdt, adj_size))

        # Enforce hard code-level circuit breaker if LLM failed to reduce size on 3+ losses
        if consec_losses >= 3 and adj_size > (max_trade_usdt * 0.6):
            adj_size = round(max_trade_usdt * 0.5, 2)
            if verdict == "APPROVE" and confidence < 0.90:
                verdict = "CAUTION"

        tp = round(float(data.get("suggested_tp_percent", dyn["dynamic_tp_percent"])), 1)
        sl = round(float(data.get("suggested_sl_percent", dyn["dynamic_sl_percent"])), 1)

        flags = data.get("guardrail_flags", [])
        if not isinstance(flags, list):
            flags = []

        # Check breaking news emergency override
        from engine.news_service import news_service
        if news_service.has_high_risk_event(pair):
            verdict = "HIGH_RISK"
            risk_score = 9
            flags.insert(0, "Breaking Emergency News Catalyst Detected")

        return {
            "verdict": verdict,
            "risk_score": risk_score,
            "confidence": confidence,
            "adjusted_position_size_usdt": adj_size,
            "suggested_tp_percent": tp,
            "suggested_sl_percent": sl,
            "historical_factor": str(data.get("historical_factor", f"Win rate: {win_rate}% across {closed} trades")),
            "guardrail_flags": [str(f) for f in flags][:5],
            "summary": str(data.get("summary", "Agentic decision evaluated."))[:300],
            "projection": proj
        }
    except Exception as e:
        logger.warning(f"Error parsing agentic decision JSON: {e}. Falling back to deterministic rules.")
        return fallback_agentic_decision(
            pair=pair, action=action, price=price, rsi=rsi, pct_b=pct_b,
            reason=reason, max_trade_usdt=max_trade_usdt, asset_history=hist,
            fng_val=fng_val, fng_class=fng_class, dyn_params=dyn
        )

async def generate_agentic_portfolio_review(
    asset_performance: dict[str, Any],
    market_context: dict[str, Any],
    api_key: str = "",
    model: str = DEFAULT_GEMINI_MODEL
) -> dict[str, Any]:
    """
    Conducts a comprehensive retrospective audit of the bot's asset performance,
    diagnoses winning vs bleeding capital allocations, and formulates strategic recommendations.
    """
    glob = asset_performance.get("global", {})
    assets = asset_performance.get("assets", {})

    asset_summaries = []
    for pair, m in assets.items():
        asset_summaries.append(
            f"- {pair}: {m['closed_trades']} closed, {m['wins']}W/{m['losses']}L ({m['win_rate']}%), "
            f"PnL: ${m['net_realized_pnl_usdt']:+.2f}, Status: {m['performance_status']}, Consec Losses: {m['consecutive_losses']}"
        )
    perf_text = "\n".join(asset_summaries) if asset_summaries else "No closed trades recorded."

    fng = await fetch_fear_and_greed_index()
    fng_str = f"{fng.get('value', 50)}/100 ({fng.get('classification', 'Neutral')})"

    fallback_review = {
        "status": "HEALTHY" if glob.get("net_realized_pnl_usdt", 0) >= 0 else "CAUTION",
        "net_pnl_usdt": glob.get("net_realized_pnl_usdt", 0.0),
        "overall_win_rate": glob.get("overall_win_rate", 0.0),
        "top_performing_asset": glob.get("best_performing_asset", "NONE"),
        "underperforming_asset": glob.get("worst_performing_asset", "NONE"),
        "key_diagnostics": [
            f"Global Win Rate: {glob.get('overall_win_rate', 0.0)}% across {glob.get('total_closed_trades', 0)} closed trades.",
            f"Macro Sentiment: {fng_str}."
        ],
        "tactical_recommendations": [
            "Maintain position sizing disciplined according to historical win rate.",
            "Throttle assets exhibiting 2+ consecutive losses."
        ]
    }

    if not api_key:
        return fallback_review

    prompt = f"""
Review this algorithmic trading bot's historical performance by asset and provide an Agentic Portfolio Audit:
- Global Performance: {glob.get('total_closed_trades', 0)} closed trades, Overall Win Rate: {glob.get('overall_win_rate', 0.0)}%, Net Realized PnL: ${glob.get('net_realized_pnl_usdt', 0.0):+.2f} USDT
- Macro Fear & Greed Index: {fng_str}
- Per-Asset Scorecards:
{perf_text}

Provide an objective strategic review in JSON:
{{
  "portfolio_health": "OPTIMAL" | "HEALTHY" | "CAUTION" | "CRITICAL",
  "key_diagnostics": [
    "Specific observation 1 on best/worst performers",
    "Specific observation 2 on win/loss patterns"
  ],
  "tactical_recommendations": [
    "Specific actionable recommendation 1",
    "Specific actionable recommendation 2"
  ]
}}
"""
    system_inst = "You are a Chief Investment Officer and Quantitative Portfolio Auditor. Return strictly valid JSON."
    raw = await call_ai(prompt, system_instruction=system_inst, api_key=api_key, model=model, json_mode=True)
    if not raw:
        return fallback_review

    try:
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned)
        return {
            "status": str(data.get("portfolio_health", "HEALTHY")).upper(),
            "net_pnl_usdt": glob.get("net_realized_pnl_usdt", 0.0),
            "overall_win_rate": glob.get("overall_win_rate", 0.0),
            "top_performing_asset": glob.get("best_performing_asset", "NONE"),
            "underperforming_asset": glob.get("worst_performing_asset", "NONE"),
            "key_diagnostics": [str(d) for d in data.get("key_diagnostics", [])][:3],
            "tactical_recommendations": [str(r) for r in data.get("tactical_recommendations", [])][:3]
        }
    except Exception:
        return fallback_review
