
from engine.db import save_config_item
from engine.state import state

async def save_strategy_state():
    state_data = {
        "dca_cooldowns": state.dca_strategy.cooldowns,
        "dca_last_trade": state.dca_strategy.last_trade_time,
        "rsi_cooldowns": state.rsi_strategy.cooldowns,
        "tpsl_cooldowns": state.tpsl_strategy.cooldowns,
    }
    await save_config_item("strategy_state", state_data)
