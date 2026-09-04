import time
import datetime
import asyncio
import logging
import aiohttp
from typing import Optional, Dict, Any

logger = logging.getLogger("CriptoBotEngine")

import functools

# Optional Discord.py imports
try:
    import discord
    from discord import app_commands
    from discord.ext import commands
    HAS_DISCORD_PY = True
except ImportError:
    HAS_DISCORD_PY = False
    app_commands = None
    logger.warning("discord.py not installed. Interactive Discord buttons disabled.")

async def guard_discord_auth(interaction: Any) -> bool:
    from engine.state import state
    user = getattr(interaction, "user", None)
    uid = getattr(user, "id", None)
    if not state.is_discord_user_authorized(uid):
        logger.warning(
            f"Blocked unauthorized Discord command from user {user} (ID: {uid}). "
            f"Allowed IDs: {state.allowed_discord_user_ids}"
        )
        msg = f"Unauthorized. Your user ID ({uid}) is not authorized for engine commands."
        resp = getattr(interaction, "response", None)
        is_done = False
        if resp and hasattr(resp, "is_done") and callable(resp.is_done):
            try:
                is_done = resp.is_done()
            except Exception:
                is_done = False

        if resp and hasattr(resp, "send_message") and not is_done:
            await resp.send_message(msg, ephemeral=True)
        elif hasattr(interaction, "followup") and hasattr(interaction.followup, "send"):
            await interaction.followup.send(msg, ephemeral=True)
        return False
    return True

def require_discord_auth(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        interaction = next((a for a in args if hasattr(a, "response") and hasattr(a, "user")), None) or kwargs.get("interaction")
        if interaction and not await guard_discord_auth(interaction):
            return
        return await func(*args, **kwargs)
    return wrapper

def build_briefing_embed(data: dict, model_name: str) -> Optional[Any]:
    if not HAS_DISCORD_PY:
        return None
    headline = data.get("headline", "Crypto Market Morning Intelligence")
    fng_str = data.get("fng_str", "50/100 (Neutral)")
    macro = data.get("macro_regime", "Market consolidating.")
    levels = data.get("key_levels", "Key levels active.")
    strategy = data.get("strategy_recommendation", "Maintain risk limits.")
    pnl = data.get("pnl_summary", {})

    embed = discord.Embed(
        title=f"[BRIEFING] {headline}",
        color=0xbd93f9,
        description="**Automated Daily Market Intelligence & Strategy Briefing**"
    )
    embed.add_field(name="Sentiment & Macro", value=f"**Fear & Greed Index:** `{fng_str}`\n{macro}", inline=False)
    embed.add_field(name="Key Watchlist Levels", value=levels, inline=False)
    embed.add_field(name="Tactical Strategy", value=strategy, inline=False)

    if pnl and pnl.get("closed_trades", 0) > 0:
        pnl_val = pnl.get("total_pnl_usdt", 0.0)
        win_rate = pnl.get("win_rate", 0.0)
        closed = pnl.get("closed_trades", 0)
        pnl_str = f"**Realized PnL:** `${pnl_val:+.2f} USDT` • **Win Rate:** `{win_rate}%` ({closed} trades)"
        embed.add_field(name="Bot Performance", value=pnl_str, inline=False)

    embed.set_footer(text=f"Model: {model_name} • Google AI Studio • Cripto-3DS Engine")
    return embed

def build_multi_trade_embed(trades: list, resolutions: dict = None) -> Optional[Any]:
    if not HAS_DISCORD_PY:
        return None
    resolutions = resolutions or {}
    total = len(trades)
    pending_count = sum(1 for t in trades if t.get("id") not in resolutions)
    approved_count = sum(1 for r in resolutions.values() if r.get("status") == "approved")
    rejected_count = sum(1 for r in resolutions.values() if r.get("status") in ("rejected", "blocked"))

    if pending_count == 0:
        if approved_count > 0 and rejected_count == 0:
            color = 0x50fa7b # All Approved (Green)
            title = f"All Trades Processed ({approved_count}/{total} Approved)"
        elif rejected_count > 0 and approved_count == 0:
            color = 0xff5555 # All Rejected (Red)
            title = f"All Trades Rejected ({rejected_count}/{total} Rejected)"
        else:
            color = 0x8be9fd # Mixed (Cyan)
            title = f"Batch Trades Resolved ({approved_count} Approved, {rejected_count} Rejected)"
    else:
        color = 0xbd93f9 # Active Purple
        title = f"Trade Confirmations Required ({pending_count}/{total} Pending)"

    embed = discord.Embed(
        title=title,
        color=color,
        description="**Multi-Asset Signal Pipeline • Table & Quick Actions**"
    )

    # Formatted ASCII/Markdown table
    table_lines = [
        "```",
        f"{'Pair':<10} {'Act':<5} {'Price':<11} {'Size':<8} {'Risk':<8} {'Status':<10}",
        "─" * 58
    ]
    for t in trades:
        tid = t.get("id")
        pair = t.get("pair", "")[:9]
        act = t.get("action", "BUY")[:4]
        price_str = f"${t.get('price', 0.0):,.2f}"[:10]
        size_str = f"${t.get('amount_usdt', 0.0):.0f}"[:7]
        
        risk_str = str(t.get("ai_risk", "MED")).split(" ")[0][:7]
        if not risk_str or risk_str == "N/A":
            risk_str = "MED"

        if tid in resolutions:
            res = resolutions[tid]
            if res.get("status") == "approved":
                status_str = "APPROVED"
            else:
                status_str = "REJECTED"
        else:
            status_str = "PENDING"

        table_lines.append(f"{pair:<10} {act:<5} {price_str:<11} {size_str:<8} {risk_str:<8} {status_str:<10}")
    table_lines.append("```")
    embed.add_field(name="Signals Overview Table", value="\n".join(table_lines), inline=False)

    # Detailed breakdown per asset
    for t in trades:
        tid = t.get("id")
        pair = t.get("pair", "")
        act = t.get("action", "BUY")
        price = t.get("price", 0.0)
        amount_usdt = t.get("amount_usdt", 0.0)
        reason = t.get("reason", "Strategy Signal")
        timeout_sec = t.get("timeout_sec", 600)
        created_at = t.get("created_at", time.time())
        exp_ts = int(created_at + timeout_sec)

        v_tag = "[BUY]" if act == "BUY" else "[SELL]"
        ai_verdict = t.get("ai_verdict", "")
        ai_risk = t.get("ai_risk", "")
        ai_summary = t.get("ai_summary", "") or t.get("analysis", "")
        ai_sl = t.get("ai_sl", "")

        field_name = f"{v_tag} {act} {pair} • ${price:,.4f} (${amount_usdt:.2f} USDT)"
        
        lines = []
        if tid in resolutions:
            res = resolutions[tid]
            if res.get("status") == "approved":
                oid = f" (Order: `{res.get('order_id')}`)" if res.get("order_id") else ""
                lines.append(f"**Status:** [APPROVED]{oid}")
            else:
                r_text = f" ({res.get('reason')})" if res.get('reason') else ""
                lines.append(f"**Status:** [REJECTED]{r_text}")
        else:
            lines.append(f"**Status:** [PENDING] • **Expires:** <t:{exp_ts}:R>")

        lines.append(f"**Reason:** {reason}")
        if ai_verdict:
            sl_info = f" • **Suggested SL:** `-{ai_sl}%`" if ai_sl else ""
            lines.append(f"**AI Analysis:** `{ai_verdict}` • **Risk:** `{ai_risk}`{sl_info}")
            if ai_summary:
                lines.append(f"*{ai_summary[:180]}*")

        embed.add_field(name=field_name, value="\n".join(lines), inline=False)

    embed.set_footer(text="Cripto-3DS Engine • Use Quick Buttons or Select Dropdown Below")
    return embed

class BatchTradeApprovalView(discord.ui.View if HAS_DISCORD_PY else object):
    def __init__(self, trades: list, timeout: float = 600):
        if HAS_DISCORD_PY:
            super().__init__(timeout=timeout)
        self.trades = trades
        self.resolutions = {}
        self.message = None
        if HAS_DISCORD_PY:
            self._build_components()

    def _build_components(self):
        self.clear_items()
        unresolved = [t for t in self.trades if t.get("id") not in self.resolutions]
        if not unresolved:
            return

        # Row 0: Quick Action Buttons (Approve All / Reject All)
        btn_approve_all = discord.ui.Button(
            label=f"Approve All ({len(unresolved)})",
            style=discord.ButtonStyle.success,
            row=0,
            custom_id="batch_approve_all"
        )
        btn_approve_all.callback = self.approve_all_callback
        self.add_item(btn_approve_all)

        btn_reject_all = discord.ui.Button(
            label=f"Reject All ({len(unresolved)})",
            style=discord.ButtonStyle.danger,
            row=0,
            custom_id="batch_reject_all"
        )
        btn_reject_all.callback = self.reject_all_callback
        self.add_item(btn_reject_all)

        # Per-Asset Controls
        if len(self.trades) <= 2:
            # 1 or 2 trades: Individual buttons per asset
            row_idx = 1
            for t in unresolved:
                tid = t.get("id")
                pair = t.get("pair", "")

                btn_app = discord.ui.Button(
                    label=f"Approve {pair}",
                    style=discord.ButtonStyle.success,
                    row=row_idx,
                    custom_id=f"app_{tid}"
                )
                btn_app.callback = self._make_single_callback(tid, True)
                self.add_item(btn_app)

                btn_rej = discord.ui.Button(
                    label=f"Reject {pair}",
                    style=discord.ButtonStyle.secondary,
                    row=row_idx,
                    custom_id=f"rej_{tid}"
                )
                btn_rej.callback = self._make_single_callback(tid, False)
                self.add_item(btn_rej)
                row_idx += 1
        else:
            # >= 3 trades: StringSelect dropdowns
            app_options = [
                discord.SelectOption(
                    label=f"Approve {t.get('pair')}",
                    value=str(t.get('id')),
                    description=f"{t.get('action')} @ ${t.get('price', 0):,.2f} (${t.get('amount_usdt', 0):.0f} USDT)"
                ) for t in unresolved[:25]
            ]
            if app_options:
                select_app = discord.ui.Select(
                    placeholder="Select asset(s) to APPROVE...",
                    min_values=1,
                    max_values=len(app_options),
                    options=app_options,
                    row=1,
                    custom_id="select_approve"
                )
                select_app.callback = self.select_approve_callback
                self.add_item(select_app)

            rej_options = [
                discord.SelectOption(
                    label=f"Reject {t.get('pair')}",
                    value=str(t.get('id')),
                    description=f"{t.get('action')} @ ${t.get('price', 0):,.2f}"
                ) for t in unresolved[:25]
            ]
            if rej_options:
                select_rej = discord.ui.Select(
                    placeholder="Select asset(s) to REJECT...",
                    min_values=1,
                    max_values=len(rej_options),
                    options=rej_options,
                    row=2,
                    custom_id="select_reject"
                )
                select_rej.callback = self.select_reject_callback
                self.add_item(select_rej)

    def _make_single_callback(self, trade_id: int, approved: bool):
        @require_discord_auth
        async def cb(interaction: discord.Interaction):
            from engine.trades import decide_trade
            await interaction.response.defer()
            res = await decide_trade(approved=approved, trade_id=trade_id)
            self.resolutions[trade_id] = res
            self._build_components()
            embed = build_multi_trade_embed(self.trades, self.resolutions)
            await interaction.message.edit(embed=embed, view=self)
        return cb

    @require_discord_auth
    async def approve_all_callback(self, interaction: discord.Interaction):
        from engine.trades import decide_trade
        await interaction.response.defer()
        unresolved = [t for t in self.trades if t.get("id") not in self.resolutions]
        for t in unresolved:
            tid = t.get("id")
            res = await decide_trade(approved=True, trade_id=tid)
            self.resolutions[tid] = res
        self._build_components()
        embed = build_multi_trade_embed(self.trades, self.resolutions)
        await interaction.message.edit(embed=embed, view=self)

    @require_discord_auth
    async def reject_all_callback(self, interaction: discord.Interaction):
        from engine.trades import decide_trade
        await interaction.response.defer()
        unresolved = [t for t in self.trades if t.get("id") not in self.resolutions]
        for t in unresolved:
            tid = t.get("id")
            res = await decide_trade(approved=False, trade_id=tid)
            self.resolutions[tid] = res
        self._build_components()
        embed = build_multi_trade_embed(self.trades, self.resolutions)
        await interaction.message.edit(embed=embed, view=self)

    @require_discord_auth
    async def select_approve_callback(self, interaction: discord.Interaction):
        from engine.trades import decide_trade
        await interaction.response.defer()
        values = interaction.data.get("values", [])
        for val in values:
            try:
                tid = int(val)
                res = await decide_trade(approved=True, trade_id=tid)
                self.resolutions[tid] = res
            except Exception:
                pass
        self._build_components()
        embed = build_multi_trade_embed(self.trades, self.resolutions)
        await interaction.message.edit(embed=embed, view=self)

    @require_discord_auth
    async def select_reject_callback(self, interaction: discord.Interaction):
        from engine.trades import decide_trade
        await interaction.response.defer()
        values = interaction.data.get("values", [])
        for val in values:
            try:
                tid = int(val)
                res = await decide_trade(approved=False, trade_id=tid)
                self.resolutions[tid] = res
            except Exception:
                pass
        self._build_components()
        embed = build_multi_trade_embed(self.trades, self.resolutions)
        await interaction.message.edit(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                embed = build_multi_trade_embed(self.trades, self.resolutions)
                await self.message.edit(content="[EXPIRED] **Trade approval request expired.**", embed=embed, view=self)
            except Exception:
                pass

class TradeApprovalView(BatchTradeApprovalView):
    def __init__(self, trade_id: int, timeout: float = 600):
        # Backward compatibility wrapper for single trade
        trade = {"id": trade_id, "pair": "TRADE", "action": "BUY", "price": 0.0, "amount_usdt": 0.0, "reason": "Trade Signal", "timeout_sec": timeout}
        super().__init__(trades=[trade], timeout=timeout)

class DiscordBotService:
    def __init__(self):
        self.client: Optional[discord.Client] = None
        self.tree: Optional[app_commands.CommandTree] = None
        self.bot_task: Optional[asyncio.Task] = None
        self.token: str = ""
        self.channel_id: str = ""
        self.ready_event: asyncio.Event = asyncio.Event()
        self.is_connecting: bool = False
        self.last_error: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        return self.ready_event.is_set()

    async def start(self, token: str, channel_id: str):
        if not HAS_DISCORD_PY:
            self.last_error = "discord.py is not installed."
            return

        clean_token = str(token or "").strip().strip('"').strip("'")
        if clean_token.startswith("Bot "):
            clean_token = clean_token[4:].strip()

        clean_channel = "".join(filter(str.isdigit, str(channel_id or "")))

        if not clean_token:
            self.last_error = "Token is empty."
            return

        if not clean_channel:
            self.last_error = "Channel ID is empty."
            return

        # Validate token structure (Discord bot tokens have 3 parts separated by dots: e.g. MTIz...GaB...fGh)
        if len(clean_token) < 45 or clean_token.count('.') < 2:
            self.last_error = "Invalid Bot Token format! Make sure you copied the Token from the 'Bot' tab (NOT Application ID or Client Secret)."
            logger.error(self.last_error)
            return

        if self.client and self.token == clean_token and self.channel_id == clean_channel:
            if self.ready_event.is_set() or self.is_connecting:
                return

        await self.stop()

        self.token = clean_token
        self.channel_id = clean_channel
        self.ready_event.clear()
        self.is_connecting = True
        self.last_error = None
        
        intents = discord.Intents.default()
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        @self.tree.command(name="status", description="Get live Cripto-3DS engine status and market indicators")
        async def cmd_status(interaction: discord.Interaction):
            from engine.state import state
            from engine.strategies import calculate_bollinger_bands
            embed = discord.Embed(
                title="Cripto-3DS Engine Status",
                color=0x50fa7b if state.is_active else 0xff5555
            )
            status_str = "ACTIVE" if state.is_active else "PAUSED"
            mode_str = "TESTNET" if state.testnet else "REAL"
            embed.add_field(name="Engine State", value=status_str, inline=True)
            embed.add_field(name="Mode", value=mode_str, inline=True)
            embed.add_field(name="USDT Available", value=f"${state.usdt_balance:.2f}", inline=True)
            
            lines = []
            for pair in state.favorite_pairs:
                price = state.prices.get(pair, 0.0)
                rsi = state.rsi_strategy.calculate_rsi(pair)
                hist = state.rsi_strategy.price_histories.get(pair, [])
                _, _, _, pct_b = calculate_bollinger_bands(hist, 20, 2.0)
                lines.append(f"**{pair}**: `${price:,.4f}` | RSI `{rsi:.1f}` | %B `{pct_b:.2f}`")
            
            if lines:
                embed.add_field(name="Live Watchlist", value="\n".join(lines), inline=False)
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="balance", description="View total portfolio net worth and holdings")
        async def cmd_balance(interaction: discord.Interaction):
            from engine.state import state
            total_val = state.usdt_balance
            holdings_lines = [f"**USDT**: `${state.usdt_balance:.2f}`"]
            
            for asset, qty in state.portfolio_balances.items():
                if asset != "USDT" and qty > 0:
                    pair = asset + "USDT"
                    price = state.prices.get(pair, 0.0)
                    val = qty * price
                    total_val += val
                    holdings_lines.append(f"**{asset}**: `{qty:.6f}` (~`${val:.2f}`)")

            embed = discord.Embed(
                title="Portfolio Balance",
                color=0x8be9fd
            )
            embed.add_field(name="Total Net Worth", value=f"**${total_val:,.2f}**", inline=False)
            embed.add_field(name="Holdings", value="\n".join(holdings_lines[:15]), inline=False)
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="start", description="Start/Activate automated trading engine")
        @require_discord_auth
        async def cmd_start(interaction: discord.Interaction):
            from engine.ws_manager import broadcast_state
            state.is_active = True
            await broadcast_state()
            await interaction.response.send_message("Trading Engine ACTIVATED.")

        @self.tree.command(name="pause", description="Pause automated trading engine")
        @require_discord_auth
        async def cmd_pause(interaction: discord.Interaction):
            from engine.ws_manager import broadcast_state
            state.is_active = False
            await broadcast_state()
            await interaction.response.send_message("Trading Engine PAUSED.")

        @self.tree.command(name="check", description="Force immediate market evaluation")
        @require_discord_auth
        async def cmd_check(interaction: discord.Interaction):
            state.rsi_strategy.cooldowns.clear()
            state.dca_strategy.cooldowns.clear()
            state.tpsl_strategy.cooldowns.clear()
            await interaction.response.send_message("Cooldown timers cleared. Evaluating market now.")

        @self.tree.command(name="testbuy", description="Simulate a test trade approval card (single or multi-asset)")
        @app_commands.describe(count="Number of simulated asset signals to test (1 to 4)")
        @require_discord_auth
        async def cmd_testbuy(interaction: discord.Interaction, count: int = 1):
            from engine.risk_manager import risk_manager
            from engine.ws_manager import broadcast_state
            await interaction.response.defer()
            
            count = max(1, min(4, count))
            test_pairs = state.favorite_pairs[:count] or ["BTCUSDT", "ETHUSDT"][:count]
            now = time.time()
            staged = []
            for i, p in enumerate(test_pairs):
                curr_price = state.prices.get(p, 0.0) or (64000.0 if "BTC" in p else (3400.0 if "ETH" in p else 150.0))
                trade_id = int(now * 1000) + i
                t = {
                    "id": trade_id,
                    "action": "BUY" if i % 2 == 0 else "SELL",
                    "pair": p,
                    "amount_usdt": risk_manager.max_trade_usdt,
                    "amount_asset": risk_manager.max_trade_usdt / curr_price,
                    "price": curr_price,
                    "reason": f"Simulated Discord /testbuy ({p})",
                    "created_at": now,
                    "timeout_sec": 600
                }
                state.add_pending_trade(t)
                staged.append(t)

            await broadcast_state()
            await self.send_interactive_trades(staged)
            await interaction.followup.send(f"Dispatched {len(staged)} test trade signal(s) in table below.")

        @self.tree.command(name="chart", description="Generate a dark-theme candlestick chart with RSI and Bollinger Bands")
        @app_commands.describe(pair="Trading pair symbol (e.g. BTC, ETH, XRPUSDT)", interval="Candlestick interval (15m, 1h, 2h, 4h, 6h, 8h, 12h, 1d)")
        @app_commands.choices(interval=[
            app_commands.Choice(name="15 Minutes", value="15m"),
            app_commands.Choice(name="1 Hour", value="1h"),
            app_commands.Choice(name="2 Hours", value="2h"),
            app_commands.Choice(name="4 Hours", value="4h"),
            app_commands.Choice(name="6 Hours", value="6h"),
            app_commands.Choice(name="8 Hours", value="8h"),
            app_commands.Choice(name="12 Hours", value="12h"),
            app_commands.Choice(name="1 Day", value="1d"),
        ])
        async def cmd_chart(interaction: discord.Interaction, pair: str = "BTCUSDT", interval: str = "4h"):
            from engine.chart_generator import fetch_klines, generate_candlestick_chart
            await interaction.response.defer()
            clean_pair = pair.upper().strip()
            if not clean_pair.endswith("USDT"):
                clean_pair += "USDT"
            
            valid_intervals = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"]
            interval_str = str(interval).lower().strip() if interval else "4h"
            if interval_str not in valid_intervals:
                interval_str = "4h"

            klines = await fetch_klines(clean_pair, interval=interval_str, limit=30)
            if not klines or len(klines) < 5:
                await interaction.followup.send(f"[ERROR] Could not fetch candlestick data for `{clean_pair}` (interval: `{interval_str}`).")
                return
            buf = await generate_candlestick_chart(clean_pair, klines, interval=interval_str)
            file = discord.File(fp=buf, filename=f"{clean_pair}_{interval_str}.png")
            embed = discord.Embed(
                title=f"{clean_pair} • {interval_str.upper()} Candlestick Chart",
                color=0x8be9fd
            )
            embed.set_image(url=f"attachment://{clean_pair}_{interval_str}.png")
            embed.set_footer(text="Cripto-3DS Engine • Live Market Analytics")
            await interaction.followup.send(embed=embed, file=file)

        @self.tree.command(name="ask", description="Ask Gemini AI about market conditions, technicals, or crypto strategies")
        @app_commands.describe(question="Your market or technical trading question")
        async def cmd_ask(interaction: discord.Interaction, question: str):
            from engine.state import state
            from engine.ai_analyst import ask_gemini
            await interaction.response.defer()
            answer = await ask_gemini(
                query=question,
                market_context=state.to_dict(),
                api_key=state.gemini_api_key,
                model=state.gemini_model
            )
            embed = discord.Embed(
                title="Gemini AI Market Analyst",
                description=answer[:4000],
                color=0xbd93f9
            )
            embed.set_footer(text=f"Model: {state.gemini_model} • Google AI Studio")
            await interaction.followup.send(f"**Q:** *{question}*", embed=embed)

        @self.tree.command(name="briefing", description="Generate live AI morning market & portfolio briefing")
        async def cmd_briefing(interaction: discord.Interaction):
            from engine.state import state
            from engine.db import get_pnl_summary
            from engine.ai_analyst import generate_market_briefing
            await interaction.response.defer()
            pnl = await get_pnl_summary(is_testnet=state.testnet)
            data = await generate_market_briefing(
                market_context=state.to_dict(),
                pnl_summary=pnl,
                api_key=state.gemini_api_key,
                model=state.gemini_model
            )
            embed = build_briefing_embed(data, state.gemini_model)
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="news", description="Fetch breaking crypto news catalysts & AI market sentiment digest")
        async def cmd_news(interaction: discord.Interaction):
            from engine.state import state
            from engine.ai_analyst import summarize_news_insights
            await interaction.response.defer()
            data = await summarize_news_insights(state.gemini_api_key, state.gemini_model)
            
            color = 0x50fa7b if data.get("overall_catalyst") == "BULLISH" else (0xff5555 if data.get("overall_catalyst") == "BEARISH" else 0xf1fa8c)
            embed = discord.Embed(
                title=f"Market News Pulse • Sentiment: {data.get('overall_catalyst', 'NEUTRAL')}",
                color=color
            )
            for i, bullet in enumerate(data.get("bullets", []), 1):
                embed.add_field(name=f"Key Catalyst #{i}", value=bullet, inline=False)
            
            headlines = data.get("headlines", [])
            if headlines:
                recent_lines = [f"• **[{h.get('asset', 'MARKET')}]** {h.get('title', '')[:80]}... (`{h.get('sentiment_tag', 'NEUTRAL')}`)" for h in headlines[:5]]
                embed.add_field(name="Recent Headlines", value="\n".join(recent_lines), inline=False)
            
            embed.set_footer(text=f"AI Sentiment Engine • Model: {state.gemini_model}")
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="opportunities", description="AI quantitative scan for dip-buys, breakout momentum & take-profit setups")
        async def cmd_opportunities(interaction: discord.Interaction):
            from engine.state import state
            from engine.ai_analyst import scan_market_opportunities
            await interaction.response.defer()
            data = await scan_market_opportunities(
                market_context=state.to_dict(),
                api_key=state.gemini_api_key,
                model=state.gemini_model
            )
            regime = data.get("market_regime", "NEUTRAL")
            color = 0x50fa7b if "BULL" in regime else (0xff5555 if "BEAR" in regime else 0x8be9fd)
            embed = discord.Embed(
                title=f"Live Market Opportunities • Regime: {regime}",
                description=f"**Fear & Greed Index:** `{data.get('fng_str', 'N/A')}`\n**Tactical Summary:** {data.get('tactical_summary', '')}",
                color=color
            )
            opps = data.get("top_opportunities", [])
            if opps:
                for o in opps:
                    stype = o.get("setup_type", "SETUP").upper()
                    if "PROFIT" in stype or "EXIT" in stype:
                        action_tag = "SELL (Take Profit)"
                    elif "DIP" in stype:
                        action_tag = "BUY (Dip Buy)"
                    else:
                        action_tag = "BUY (Breakout)"

                    conf = int(float(o.get("confidence", 0.8)) * 100)
                    embed.add_field(
                        name=f"[{action_tag}] {o.get('pair', '')} ({conf}% Confidence)",
                        value=f"**Levels:** `{o.get('key_levels', 'N/A')}`\n{o.get('analysis', '')}",
                        inline=False
                    )
            else:
                embed.add_field(name="Active Setups", value="No extreme oversold or overbought setups currently triggered. Market consolidating in balance.", inline=False)
            
            embed.set_footer(text=f"AI Quant Scanner • Model: {state.gemini_model}")
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="test", description="Run full unit & integration test suite on server")
        @require_discord_auth
        async def cmd_test(interaction: discord.Interaction):
            import asyncio, time, sys
            await interaction.response.defer()
            start = time.time()
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pytest", "tests/test_engine.py", "-k", "not test_api_run_test_suite", "-v",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            duration = round(time.time() - start, 2)
            output = stdout.decode() + stderr.decode()
            passed = output.count("PASSED")
            
            color = 0x50fa7b if proc.returncode == 0 else 0xff5555
            embed = discord.Embed(
                title=f"Test Suite Results: {'PASSED' if proc.returncode == 0 else 'FAILED'}",
                description=f"Executed **{passed}** tests in **{duration}s** with exit code **{proc.returncode}**.",
                color=color
            )
            embed.add_field(name="Summary", value=f"```\n{output[-600:]}\n```", inline=False)
            embed.set_footer(text="Cripto-3DS Engine Remote Test Runner")
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="cleartrades", description="Purge unwanted trade records from database")
        @app_commands.describe(all_trades="Set true to wipe entire ledger, false to only clean rejected/test trades")
        @require_discord_auth
        async def cmd_cleartrades(interaction: discord.Interaction, all_trades: bool = False):
            from engine.db import clear_trade_history
            from engine.ws_manager import broadcast_state
            await interaction.response.defer()
            deleted = await clear_trade_history(only_unexecuted=not all_trades, is_testnet=state.testnet)
            await broadcast_state()
            mode_str = "All Trades" if all_trades else "Rejected & Test Trades"
            await interaction.followup.send(f"Cleaned **{deleted}** {mode_str} from database.")

        @self.tree.command(name="sync2026", description="Backfill all Binance trade fills from 2026 to present into database")
        @require_discord_auth
        async def cmd_sync2026(interaction: discord.Interaction):
            await interaction.response.defer()
            from engine.trades import sync_binance_2026_trades
            res = await sync_binance_2026_trades()
            imported = res.get("imported", 0)
            await interaction.followup.send(f"2026 Binance Sync complete. Imported {imported} trade records into database.")

        @self.client.event
        async def on_ready():
            self.ready_event.set()
            self.is_connecting = False
            self.last_error = None
            logger.info(f"Discord Bot connected as {self.client.user} (ID: {self.client.user.id})")
            try:
                clean_channel_id = int(self.channel_id)
                ch = self.client.get_channel(clean_channel_id)
                if not ch:
                    try:
                        ch = await self.client.fetch_channel(clean_channel_id)
                    except Exception:
                        pass

                if ch and hasattr(ch, "guild") and ch.guild:
                    self.tree.copy_global_to(guild=ch.guild)
                    synced = await self.tree.sync(guild=ch.guild)
                    logger.info(f"Instantly synced {len(synced)} slash commands directly to server: {ch.guild.name} ({ch.guild.id})")
                else:
                    synced = await self.tree.sync()
                    logger.info(f"Synced {len(synced)} global Discord slash commands.")
            except Exception as e:
                logger.warning(f"Failed to sync slash commands: {e}")

        async def daily_briefing_loop():
            # Wait for bot gateway readiness
            await self.ready_event.wait()
            while True:
                try:
                    now_utc = datetime.datetime.now(datetime.timezone.utc)
                    # 08:00 AM Peru (UTC-5) is 13:00 UTC
                    target_today = now_utc.replace(hour=13, minute=0, second=0, microsecond=0)
                    if now_utc >= target_today:
                        target_next = target_today + datetime.timedelta(days=1)
                    else:
                        target_next = target_today
                    
                    wait_sec = max(60.0, (target_next - now_utc).total_seconds())
                    logger.info(f"Daily morning briefing scheduled in {wait_sec/3600:.1f}h (08:00 AM Peru / 13:00 UTC).")
                    await asyncio.sleep(wait_sec)
                    await self.broadcast_daily_briefing()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Error in daily briefing scheduler loop: {e}")
                    await asyncio.sleep(3600)

        async def run_bot():
            try:
                logger.info(f"Connecting to Discord Gateway (token: {self.token[:8]}...)...")
                await self.client.start(self.token)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                logger.error(f"Discord Bot connection error: {self.last_error}")
            finally:
                self.is_connecting = False
                self.ready_event.clear()

        self.bot_task = asyncio.create_task(run_bot())
        self.briefing_task = asyncio.create_task(daily_briefing_loop())

    async def broadcast_daily_briefing(self):
        if not self.is_ready or not self.client:
            return
        from engine.state import state
        from engine.db import get_pnl_summary
        from engine.ai_analyst import generate_market_briefing
        try:
            clean_channel_id = int(self.channel_id)
            ch = self.client.get_channel(clean_channel_id)
            if not ch:
                ch = await self.client.fetch_channel(clean_channel_id)
            if ch:
                pnl = await get_pnl_summary(is_testnet=state.testnet)
                data = await generate_market_briefing(
                    market_context=state.to_dict(),
                    pnl_summary=pnl,
                    api_key=state.gemini_api_key,
                    model=state.gemini_model
                )
                embed = build_briefing_embed(data, state.gemini_model)
                await ch.send(content="**Good Morning! Here is your Daily Cripto-3DS Market Briefing:**", embed=embed)
                logger.info("Daily morning briefing posted to Discord channel.")
        except Exception as e:
            logger.error(f"Failed to post daily morning briefing: {e}")

    async def stop(self):
        self.ready_event.clear()
        self.is_connecting = False
        if hasattr(self, "briefing_task") and self.briefing_task:
            self.briefing_task.cancel()
            self.briefing_task = None
        if self.client and not self.client.is_closed():
            try:
                await self.client.close()
            except Exception:
                pass
        if self.bot_task:
            self.bot_task.cancel()
            self.bot_task = None
        self.client = None

    async def send_interactive_trades(self, trades: list) -> bool:
        if not HAS_DISCORD_PY or not trades:
            return False

        from engine.state import state
        token = self.token or state.discord_bot_token
        channel_id = self.channel_id or state.discord_channel_id

        if not token or not channel_id:
            logger.debug("Discord Bot Token or Channel ID missing.")
            return False

        if not self.client or (not self.is_ready and not self.is_connecting):
            logger.info("Starting Discord Bot connection...")
            await self.start(token, channel_id)

        try:
            if not self.ready_event.is_set():
                logger.info("Waiting up to 10s for Discord Bot Gateway connection...")
                try:
                    await asyncio.wait_for(self.ready_event.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("Discord Bot Gateway connection timed out.")
                    return False

            clean_channel_id = int("".join(filter(str.isdigit, str(channel_id))))
            channel = self.client.get_channel(clean_channel_id)
            if not channel:
                channel = await self.client.fetch_channel(clean_channel_id)

            if not channel:
                logger.warning(f"Discord channel {clean_channel_id} not found.")
                return False

            # Run AI analysis across all trades concurrently if Gemini API key is configured
            if state.gemini_api_key:
                from engine.ai_analyst import analyze_trade_signal
                from engine.strategies import calculate_bollinger_bands

                async def _analyze_trade(t):
                    if t.get("ai_verdict"):
                        return
                    pair = t.get("pair", "BTCUSDT")
                    rsi_val = state.rsi_strategy.calculate_rsi(pair)
                    hist = state.rsi_strategy.price_histories.get(pair, [])
                    _, _, _, pct_b = calculate_bollinger_bands(hist, 20, 2.0)
                    ai_data = await analyze_trade_signal(
                        pair=pair,
                        action=t.get("action", "BUY"),
                        price=t.get("price", 0.0),
                        rsi=rsi_val,
                        pct_b=pct_b,
                        reason=t.get("reason", ""),
                        price_history=hist,
                        api_key=state.gemini_api_key,
                        model=state.gemini_model
                    )
                    if isinstance(ai_data, dict):
                        verdict = ai_data.get("verdict", "CAUTION")
                        risk_score = ai_data.get("risk_score", 5)
                        risk_label = "LOW" if risk_score <= 3 else ("MEDIUM" if risk_score <= 6 else "HIGH")
                        t["ai_verdict"] = verdict
                        t["ai_risk"] = f"{risk_label} ({risk_score}/10)"
                        t["ai_sl"] = ai_data.get("suggested_sl_percent", 3.0)
                        t["ai_summary"] = ai_data.get("summary", "")
                        t["fng"] = f"{ai_data.get('fng_index', 50)}/100 ({ai_data.get('fng_classification', 'Neutral')})"
                        t["red_flags"] = ai_data.get("red_flags", [])

                await asyncio.gather(*[_analyze_trade(t) for t in trades], return_exceptions=True)

            timeout_sec = max((t.get("timeout_sec", 600) for t in trades), default=600)
            embed = build_multi_trade_embed(trades)
            view = BatchTradeApprovalView(trades=trades, timeout=timeout_sec)
            msg = await channel.send(embed=embed, view=view)
            view.message = msg
            logger.info(f"Consolidated interactive Discord trade table card sent ({len(trades)} assets).")
            return True
        except Exception as e:
            logger.error(f"Failed to send interactive Discord trade table: {e}")
            return False

    async def send_interactive_trade(self, trade: dict) -> bool:
        return await self.send_interactive_trades([trade])

discord_bot_service = DiscordBotService()

async def send_discord_notification(subject: str, body: str, config: dict, trade: dict = None, trades: list = None):
    trade_list = trades or ([trade] if trade else None)
    if trade_list:
        sent = await discord_bot_service.send_interactive_trades(trade_list)
        if sent:
            return

    # 2. Fallback to webhook if configured
    webhook_url = config.get("discord_webhook_url")
    if not webhook_url:
        return
    
    if trade_list:
        table_lines = [
            f"**{subject}**",
            "```",
            f"{'Pair':<10} {'Act':<5} {'Price':<11} {'Size':<8} {'Reason'}",
            "─" * 50
        ]
        for t in trade_list:
            table_lines.append(f"{t.get('pair',''):<10} {t.get('action',''):<5} ${t.get('price',0):<10.2f} ${t.get('amount_usdt',0):<7.0f} {t.get('reason','')[:20]}")
        table_lines.append("```")
        content = "\n".join(table_lines)
    else:
        content = f"**{subject}**\n{body}"

    payload = {"content": content}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status in (200, 204):
                    logger.info("Discord Webhook notification sent.")
                else:
                    logger.error(f"Discord Webhook notification failed. Status: {resp.status}")
    except Exception as e:
        logger.error(f"Error sending Discord Webhook: {e}")

