import pytest
from httpx import ASGITransport, AsyncClient

from engine.db import init_db
from main import app, state

pytestmark = pytest.mark.unit


def test_project_trade_outcome_buy():
    from engine.agentic_decision import project_trade_outcome

    trade_buy = {
        "action": "BUY",
        "price": 100.0,
        "amount_usdt": 100.0
    }
    proj = project_trade_outcome(trade_buy, cost_basis=0.0, current_holdings=0.0, dynamic_tp=5.0, dynamic_sl=2.5)
    assert proj["action"] == "BUY"
    assert proj["target_profit_usdt"] == 5.0
    assert proj["max_risk_usdt"] == 2.5
    assert proj["target_tp_price"] == 105.0
    assert proj["stop_loss_price"] == 97.5
    assert proj["risk_reward_ratio"] == 2.0
    assert "Target +$5.00" in proj["summary_str"]
    assert "Risk -$2.50" in proj["summary_str"]

    # Test blended cost basis on adding to position
    proj_blended = project_trade_outcome(
        trade_buy,
        cost_basis=80.0,
        current_holdings=1.0,
        dynamic_tp=5.0,
        dynamic_sl=2.5
    )
    # 1.0 held @ $80 ($80) + 1.0 bought @ $100 ($100) = 2.0 held @ $90
    assert proj_blended["blended_cost_basis"] == 90.0

def test_project_trade_outcome_sell():
    from engine.agentic_decision import project_trade_outcome

    # Profitable sell
    trade_profit = {
        "action": "SELL",
        "price": 150.0,
        "amount_asset": 2.0
    }
    proj_win = project_trade_outcome(trade_profit, cost_basis=100.0)
    assert proj_win["action"] == "SELL"
    assert proj_win["projected_pnl_usdt"] == 100.0
    assert proj_win["projected_pnl_percent"] == 50.0
    assert proj_win["outcome_type"] == "PROFIT"
    assert "Locks in +$100.00 USDT (+50.0%)" in proj_win["summary_str"]

    # Losing sell
    trade_loss = {
        "action": "SELL",
        "price": 80.0,
        "amount_asset": 2.0
    }
    proj_loss = project_trade_outcome(trade_loss, cost_basis=100.0)
    assert proj_loss["action"] == "SELL"
    assert proj_loss["projected_pnl_usdt"] == -40.0
    assert proj_loss["projected_pnl_percent"] == -20.0
    assert proj_loss["outcome_type"] == "LOSS"
    assert "Realizes -$40.00 USDT (-20.0%)" in proj_loss["summary_str"]

    # Breakeven sell
    trade_be = {
        "action": "SELL",
        "price": 100.0,
        "amount_asset": 2.0
    }
    proj_be = project_trade_outcome(trade_be, cost_basis=100.0)
    assert proj_be["outcome_type"] == "BREAKEVEN"

@pytest.mark.asyncio
async def test_agentic_decision_with_history_and_circuit_breaker():
    from engine.agentic_decision import (
        calculate_dynamic_trade_parameters,
        evaluate_agentic_trade_decision,
        fallback_agentic_decision,
    )

    # 1. Dynamic params normal
    dyn_normal = calculate_dynamic_trade_parameters(100.0, 30.0, 0.2, consecutive_losses=0, win_rate=75.0)
    assert dyn_normal["dynamic_tp_percent"] >= 5.0

    # 2. Dynamic params with loss streak
    dyn_throttled = calculate_dynamic_trade_parameters(100.0, 30.0, 0.2, consecutive_losses=3, win_rate=25.0)
    assert dyn_throttled["dynamic_sl_percent"] <= 2.0

    # 3. Fallback agentic decision with consecutive losses >= 3
    hist_loss = {
        "closed_trades": 8,
        "win_rate": 25.0,
        "consecutive_losses": 3,
        "net_realized_pnl_usdt": -25.0,
        "performance_status": "DRAWDOWN"
    }
    dec_loss = fallback_agentic_decision(
        pair="BTCUSDT",
        action="BUY",
        price=60000.0,
        rsi=28.0,
        pct_b=0.15,
        reason="RSI Oversold",
        max_trade_usdt=20.0,
        asset_history=hist_loss
    )
    assert dec_loss["adjusted_position_size_usdt"] == 10.0  # 50% throttled
    assert any("LOSS_STREAK" in flag for flag in dec_loss["guardrail_flags"])

    # 4. Evaluate with empty API key falls back smoothly
    dec_eval = await evaluate_agentic_trade_decision(
        pair="ETHUSDT",
        action="BUY",
        price=3000.0,
        rsi=30.0,
        pct_b=0.2,
        reason="DCA",
        max_trade_usdt=20.0,
        asset_history=hist_loss,
        api_key=""
    )
    assert dec_eval["adjusted_position_size_usdt"] == 10.0

@pytest.mark.asyncio
async def test_agentic_portfolio_review():
    from engine.agentic_decision import generate_agentic_portfolio_review

    sample_perf = {
        "global": {
            "total_trades": 10,
            "total_closed_trades": 5,
            "overall_win_rate": 80.0,
            "net_realized_pnl_usdt": 35.50,
            "best_performing_asset": "SOL (+43.50)",
            "worst_performing_asset": "NONE"
        },
        "assets": {
            "SOLUSDT": {
                "asset": "SOL",
                "closed_trades": 5,
                "wins": 4,
                "losses": 1,
                "win_rate": 80.0,
                "net_realized_pnl_usdt": 35.50,
                "performance_status": "HIGH_PROFIT",
                "consecutive_losses": 0
            }
        }
    }

    review = await generate_agentic_portfolio_review(sample_perf, {}, api_key="")
    assert "status" in review
    assert "tactical_recommendations" in review
    assert review["net_pnl_usdt"] == 35.50

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/ai/portfolio_review", headers={"X-Auth-PIN": state.auth_pin})
        assert res.status_code == 200
        data = res.json()
        assert "status" in data
