import asyncio
import datetime
import logging
import time
from typing import Any, Dict, Optional

import aiohttp

from engine.discord_views import (
    HAS_DISCORD_PY,
    BatchTradeApprovalView,
    TradeApprovalView,
    build_briefing_embed,
    build_multi_trade_embed,
    guard_discord_auth,
    require_discord_auth,
)
from engine.state import state

logger = logging.getLogger("CriptoBotEngine")

try:
    import discord
    from discord import app_commands
    from discord.ext import commands
except ImportError:
    discord = None
    app_commands = None


class DiscordBotService:
    def __init__(self):
        self.client: discord.Client | None = None
        self.tree: app_commands.CommandTree | None = None
        self.bot_task: asyncio.Task | None = None
        self.token: str = ""
        self.channel_id: str = ""
        self.ready_event: asyncio.Event = asyncio.Event()
        self.is_connecting: bool = False
        self.last_error: str | None = None

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
        if len(clean_token) < 45 or clean_token.count(".") < 2:
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

            embed = discord.Embed(title="Cripto-3DS Engine Status", color=0x50FA7B if state.is_active else 0xFF5555)
            status_str = "ACTIVE" if state.is_active else "PAUSED"
            mode_str = "TESTNET" if state.testnet else "REAL"
            embed.add_field(name="Engine State", value=status_str, inline=True)
            embed.add_field(name="Mode", value=mode_str, inline=True)
            embed.add_field(name="USDT Available", value=f"${state.usdt_balance:.2f}", inline=True)
            embed.add_field(name="AI Provider", value=f"{state.ai_provider.upper()} ({state.ai_model})", inline=True)

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

            embed = discord.Embed(title="Portfolio Balance", color=0x8BE9FD)
            glob = state.asset_performance.get("global", {})
            if glob:
                embed.add_field(
                    name="Realized Performance",
                    value=f"PnL: **${glob.get('net_realized_pnl_usdt', 0.0):+.2f}** | Win Rate: **{glob.get('overall_win_rate', 0.0)}%** ({glob.get('total_wins', 0)}W/{glob.get('total_losses', 0)}L)",
                    inline=False,
                )

            embed.add_field(name="Total Net Worth", value=f"**${total_val:,.2f}**", inline=False)
            embed.add_field(name="Holdings", value="\n".join(holdings_lines[:15]), inline=False)
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="performance", description="View asset-by-asset win/loss performance and scorecards")
        async def cmd_performance(interaction: discord.Interaction):
            from engine.history_analyzer import analyze_and_reconcile_history
            from engine.state import state

            if not state.asset_performance:
                state.asset_performance = await analyze_and_reconcile_history(state.testnet, update_db=False)

            perf = state.asset_performance
            glob = perf.get("global", {})
            assets = perf.get("assets", {})

            embed = discord.Embed(
                title="Performance & Win/Loss Scorecard",
                color=0x50FA7B if glob.get("net_realized_pnl_usdt", 0) >= 0 else 0xFF5555,
            )
            embed.add_field(
                name="Global Summary",
                value=(
                    f"Realized PnL: **${glob.get('net_realized_pnl_usdt', 0.0):+.2f} USDT**\n"
                    f"Win Rate: **{glob.get('overall_win_rate', 0.0)}%** ({glob.get('total_wins', 0)}W / {glob.get('total_losses', 0)}L / {glob.get('total_breakeven', 0)}BE)\n"
                    f"Closed Trades: **{glob.get('total_closed_trades', 0)}** | Profit Factor: **{glob.get('portfolio_profit_factor', 0.0)}**\n"
                    f"Best Asset: **{glob.get('best_performing_asset', 'NONE')}** | Underperforming: **{glob.get('worst_performing_asset', 'NONE')}**"
                ),
                inline=False,
            )

            asset_lines = []
            for pair, m in assets.items():
                if m.get("closed_trades", 0) > 0 or m.get("current_position_qty", 0) > 0:
                    streak_str = f" | Streak: {m['consecutive_losses']}L" if m.get("consecutive_losses", 0) >= 2 else ""
                    asset_lines.append(
                        f"**{m['asset']}**: {m['closed_trades']} trades | **{m['win_rate']}% WR** ({m['wins']}W/{m['losses']}L) | "
                        f"PnL: **${m['net_realized_pnl_usdt']:+.2f}** | Status: `{m['performance_status']}`{streak_str}"
                    )
            if asset_lines:
                embed.add_field(name="Asset Breakdown", value="\n".join(asset_lines[:10]), inline=False)
            else:
                embed.add_field(name="Asset Breakdown", value="No asset trades recorded yet.", inline=False)

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

        @self.tree.command(name="dca", description="Toggle or set DCA strategy status")
        @app_commands.describe(enabled="Set DCA active (True) or inactive (False). Leave empty to toggle.")
        @require_discord_auth
        async def cmd_dca(interaction: discord.Interaction, enabled: bool | None = None):
            from engine.db import load_config_item, save_config_item
            from engine.ws_manager import broadcast_state

            if enabled is None:
                state.dca_strategy.enabled = not state.dca_strategy.enabled
            else:
                state.dca_strategy.enabled = enabled

            saved_cfg = await load_config_item("risk_config") or {}
            saved_cfg["dca_enabled"] = state.dca_strategy.enabled
            await save_config_item("risk_config", saved_cfg)
            await broadcast_state()

            status_str = "ENABLED" if state.dca_strategy.enabled else "DISABLED"
            await interaction.response.send_message(
                f"DCA Strategy is now **{status_str}** (Interval: {state.dca_strategy.interval_sec}s)."
            )

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
                    "timeout_sec": 600,
                }
                state.add_pending_trade(t)
                staged.append(t)

            await broadcast_state()
            await self.send_interactive_trades(staged)
            await interaction.followup.send(f"Dispatched {len(staged)} test trade signal(s) in table below.")

        @self.tree.command(
            name="chart", description="Generate a dark-theme candlestick chart with RSI and Bollinger Bands"
        )
        @app_commands.describe(
            pair="Trading pair symbol (e.g. BTC, ETH, XRPUSDT)",
            interval="Candlestick interval (15m, 1h, 2h, 4h, 6h, 8h, 12h, 1d)",
        )
        @app_commands.choices(
            interval=[
                app_commands.Choice(name="15 Minutes", value="15m"),
                app_commands.Choice(name="1 Hour", value="1h"),
                app_commands.Choice(name="2 Hours", value="2h"),
                app_commands.Choice(name="4 Hours", value="4h"),
                app_commands.Choice(name="6 Hours", value="6h"),
                app_commands.Choice(name="8 Hours", value="8h"),
                app_commands.Choice(name="12 Hours", value="12h"),
                app_commands.Choice(name="1 Day", value="1d"),
            ]
        )
        async def cmd_chart(interaction: discord.Interaction, pair: str = "BTCUSDT", interval: str = "4h"):
            from engine.chart_generator import fetch_klines, generate_candlestick_chart

            await interaction.response.defer()
            clean_pair = pair.upper().strip()
            if not clean_pair.endswith("USDT"):
                clean_pair += "USDT"

            valid_intervals = [
                "1m",
                "3m",
                "5m",
                "15m",
                "30m",
                "1h",
                "2h",
                "4h",
                "6h",
                "8h",
                "12h",
                "1d",
                "3d",
                "1w",
                "1M",
            ]
            interval_str = str(interval).lower().strip() if interval else "4h"
            if interval_str not in valid_intervals:
                interval_str = "4h"

            klines = await fetch_klines(clean_pair, interval=interval_str, limit=30)
            if not klines or len(klines) < 5:
                await interaction.followup.send(
                    f"[ERROR] Could not fetch candlestick data for `{clean_pair}` (interval: `{interval_str}`)."
                )
                return
            buf = await generate_candlestick_chart(clean_pair, klines, interval=interval_str)
            file = discord.File(fp=buf, filename=f"{clean_pair}_{interval_str}.png")
            embed = discord.Embed(title=f"{clean_pair} • {interval_str.upper()} Candlestick Chart", color=0x8BE9FD)
            embed.set_image(url=f"attachment://{clean_pair}_{interval_str}.png")
            embed.set_footer(text="Cripto-3DS Engine • Live Market Analytics")
            await interaction.followup.send(embed=embed, file=file)

        @self.tree.command(
            name="ask", description="Ask AI Analyst about market conditions, technicals, or crypto strategies"
        )
        @app_commands.describe(question="Your market or technical trading question")
        async def cmd_ask(interaction: discord.Interaction, question: str):
            from engine.ai_session import execute_chat_turn
            from engine.state import state

            await interaction.response.defer()
            sid = f"discord_{interaction.channel_id}_{interaction.user.id}"
            key = getattr(state, "ai_api_key", "") or getattr(state, "gemini_api_key", "")
            fb_key = getattr(state, "ai_fallback_api_key", "") or getattr(state, "groq_api_key", "")
            res = await execute_chat_turn(
                query=question,
                session_id=sid,
                market_context=state.to_dict(),
                provider=getattr(state, "ai_provider", "google"),
                model=getattr(state, "ai_model", "gemini-3.1-flash"),
                api_key=key,
                base_url=getattr(state, "ai_base_url", ""),
                fallback_provider=getattr(state, "ai_fallback_provider", "groq"),
                fallback_model=getattr(state, "ai_fallback_model", "llama-3.3-70b-versatile"),
                fallback_api_key=fb_key,
            )
            from engine.ai_provider import extract_text_from_ai_message

            raw_ans = res.get("answer", "")
            answer = extract_text_from_ai_message(raw_ans)
            turns = res.get("turn_count", 1)
            prov = getattr(state, "ai_provider", "google")
            prov_display = (
                "OpenAI" if prov.lower() == "openai" else ("Ollama" if prov.lower() == "ollama" else prov.title())
            )
            model_display = getattr(state, "ai_model", "gemini-3.1-flash")

            if len(answer) > 4000:
                answer = answer[:3950] + "\n\n*(Truncated to fit Discord embed limit)*"

            embed = discord.Embed(title=f"{prov_display} AI Market Analyst", description=answer, color=0xBD93F9)
            embed.set_footer(text=f"Model: {model_display} • Provider: {prov_display} • Turn #{turns}")
            await interaction.followup.send(f"**Q:** *{question}*", embed=embed)

        @self.tree.command(name="clearsession", description="Clear your conversation memory and reset AI session")
        async def cmd_clearsession(interaction: discord.Interaction):
            from engine.ai_session import session_manager

            sid = f"discord_{interaction.channel_id}_{interaction.user.id}"
            cleared = session_manager.clear_session(sid)
            if cleared:
                await interaction.response.send_message(
                    "AI conversation session cleared for this channel. Memory reset.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "No active conversation session found to clear.", ephemeral=True
                )

        @self.tree.command(name="briefing", description="Generate live AI morning market & portfolio briefing")
        async def cmd_briefing(interaction: discord.Interaction):
            from engine.ai_analyst import generate_market_briefing
            from engine.db import get_pnl_summary
            from engine.state import state

            await interaction.response.defer()
            pnl = await get_pnl_summary(is_testnet=state.testnet)
            data = await generate_market_briefing(
                market_context=state.to_dict(), pnl_summary=pnl, api_key=state.gemini_api_key, model=state.gemini_model
            )
            embed = build_briefing_embed(data, state.gemini_model)
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="news", description="Fetch breaking crypto news catalysts & AI market sentiment digest")
        async def cmd_news(interaction: discord.Interaction):
            from engine.ai_analyst import summarize_news_insights
            from engine.state import state

            await interaction.response.defer()
            data = await summarize_news_insights(state.gemini_api_key, state.gemini_model)

            color = (
                0x50FA7B
                if data.get("overall_catalyst") == "BULLISH"
                else (0xFF5555 if data.get("overall_catalyst") == "BEARISH" else 0xF1FA8C)
            )
            embed = discord.Embed(
                title=f"Market News Pulse • Sentiment: {data.get('overall_catalyst', 'NEUTRAL')}", color=color
            )
            for i, bullet in enumerate(data.get("bullets", []), 1):
                embed.add_field(name=f"Key Catalyst #{i}", value=bullet, inline=False)

            headlines = data.get("headlines", [])
            if headlines:
                recent_lines = [
                    f"• **[{h.get('asset', 'MARKET')}]** {h.get('title', '')[:80]}... (`{h.get('sentiment_tag', 'NEUTRAL')}`)"
                    for h in headlines[:5]
                ]
                embed.add_field(name="Recent Headlines", value="\n".join(recent_lines), inline=False)

            embed.set_footer(text=f"AI Sentiment Engine • Model: {state.gemini_model}")
            await interaction.followup.send(embed=embed)

        @self.tree.command(
            name="opportunities",
            description="AI quantitative scan for dip-buys, breakout momentum & take-profit setups",
        )
        async def cmd_opportunities(interaction: discord.Interaction):
            from engine.ai_analyst import scan_market_opportunities
            from engine.state import state

            await interaction.response.defer()
            data = await scan_market_opportunities(
                market_context=state.to_dict(), api_key=state.gemini_api_key, model=state.gemini_model
            )
            regime = data.get("market_regime", "NEUTRAL")
            color = 0x50FA7B if "BULL" in regime else (0xFF5555 if "BEAR" in regime else 0x8BE9FD)
            embed = discord.Embed(
                title=f"Live Market Opportunities • Regime: {regime}",
                description=f"**Fear & Greed Index:** `{data.get('fng_str', 'N/A')}`\n**Tactical Summary:** {data.get('tactical_summary', '')}",
                color=color,
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
                        inline=False,
                    )
            else:
                embed.add_field(
                    name="Active Setups",
                    value="No extreme oversold or overbought setups currently triggered. Market consolidating in balance.",
                    inline=False,
                )

            embed.set_footer(text=f"AI Quant Scanner • Model: {state.gemini_model}")
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="test", description="Run full unit & integration test suite on server")
        @require_discord_auth
        async def cmd_test(interaction: discord.Interaction):
            import asyncio
            import sys
            import time

            await interaction.response.defer()
            start = time.time()
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pytest",
                "tests",
                "-k",
                "not test_api_run_test_suite",
                "-v",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            duration = round(time.time() - start, 2)
            output = stdout.decode() + stderr.decode()
            passed = output.count("PASSED")

            color = 0x50FA7B if proc.returncode == 0 else 0xFF5555
            embed = discord.Embed(
                title=f"Test Suite Results: {'PASSED' if proc.returncode == 0 else 'FAILED'}",
                description=f"Executed **{passed}** tests in **{duration}s** with exit code **{proc.returncode}**.",
                color=color,
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

        @self.tree.command(
            name="sync2026", description="Backfill all Binance trade fills from 2026 to present into database"
        )
        @require_discord_auth
        async def cmd_sync2026(interaction: discord.Interaction):
            await interaction.response.defer()
            from engine.trades import sync_binance_2026_trades

            res = await sync_binance_2026_trades()
            imported = res.get("imported", 0)
            await interaction.followup.send(
                f"2026 Binance Sync complete. Imported {imported} trade records into database."
            )

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
                    logger.info(
                        f"Instantly synced {len(synced)} slash commands directly to server: {ch.guild.name} ({ch.guild.id})"
                    )
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
                    logger.info(
                        f"Daily morning briefing scheduled in {wait_sec / 3600:.1f}h (08:00 AM Peru / 13:00 UTC)."
                    )
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
        from engine.ai_analyst import generate_market_briefing
        from engine.db import get_pnl_summary
        from engine.state import state

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
                    model=state.gemini_model,
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

            # Run AI analysis or quantitative math across all trades concurrently
            from engine.agentic_decision import calculate_dynamic_trade_parameters, project_trade_outcome
            from engine.ai_analyst import analyze_trade_signal, fallback_trade_signal_analysis
            from engine.strategies import calculate_bollinger_bands

            async def _analyze_trade(t):
                if t.get("ai_verdict"):
                    return
                pair = t.get("pair", "BTCUSDT")
                rsi_val = state.rsi_strategy.calculate_rsi(pair)
                hist = state.rsi_strategy.price_histories.get(pair, [])
                _, _, _, pct_b = calculate_bollinger_bands(hist, 20, 2.0)

                asset_sym = pair.replace("USDT", "")
                cost_basis = float(state.cost_bases.get(pair, 0.0))
                holdings = float(state.portfolio_balances.get(asset_sym, 0.0))
                asset_perf = state.asset_performance.get("assets", {}).get(pair, {})
                dyn_params = calculate_dynamic_trade_parameters(pair, asset_perf, pct_b=pct_b)

                if not t.get("projection"):
                    t["projection"] = project_trade_outcome(
                        t,
                        cost_basis=cost_basis,
                        current_holdings=holdings,
                        dynamic_tp=dyn_params["dynamic_tp_percent"],
                        dynamic_sl=dyn_params["dynamic_sl_percent"],
                    )

                extra_ctx = {
                    "asset_history": asset_perf,
                    "cost_basis": cost_basis,
                    "current_holdings": holdings,
                    "projection": t.get("projection"),
                    "amount_usdt": t.get("amount_usdt", 0.0),
                    "amount_asset": t.get("amount_asset", 0.0),
                }

                ai_data = None
                if state.has_ai:
                    try:
                        ai_data = await asyncio.wait_for(
                            analyze_trade_signal(
                                pair=pair,
                                action=t.get("action", "BUY"),
                                price=t.get("price", 0.0),
                                rsi=rsi_val,
                                pct_b=pct_b,
                                reason=t.get("reason", ""),
                                price_history=hist,
                                api_key=state.ai_api_key or state.gemini_api_key,
                                model=state.ai_model or state.gemini_model,
                                extra_context=extra_ctx,
                            ),
                            timeout=4.5,
                        )
                    except Exception as e:
                        logger.warning(f"AI trade analysis timeout/error on {pair}: {e}. Engaging math fallback.")
                        ai_data = None

                if not isinstance(ai_data, dict):
                    ai_data = await fallback_trade_signal_analysis(
                        pair=pair,
                        action=t.get("action", "BUY"),
                        price=t.get("price", 0.0),
                        rsi=rsi_val,
                        pct_b=pct_b,
                        reason=t.get("reason", ""),
                        price_history=hist,
                        extra_context=extra_ctx,
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
                    if ai_data.get("projection"):
                        t["projection"] = ai_data.get("projection")

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
            "─" * 50,
        ]
        for t in trade_list:
            table_lines.append(
                f"{t.get('pair', ''):<10} {t.get('action', ''):<5} ${t.get('price', 0):<10.2f} ${t.get('amount_usdt', 0):<7.0f} {t.get('reason', '')[:20]}"
            )
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
