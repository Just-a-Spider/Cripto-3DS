import time
import asyncio
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger("CriptoBotEngine")

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

class TradeApprovalView(discord.ui.View if HAS_DISCORD_PY else object):
    def __init__(self, trade_id: int, timeout: float = 600):
        if HAS_DISCORD_PY:
            super().__init__(timeout=timeout)
        self.trade_id = trade_id
        self.message = None

    if HAS_DISCORD_PY:
        @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
        async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            from engine.trades import decide_trade
            await interaction.response.defer()
            res = await decide_trade(approved=True)
            
            for child in self.children:
                child.disabled = True
            
            status_text = "APPROVED" if res.get("status") == "approved" else f"BLOCKED: {res.get('reason', 'Unknown')}"
            order_info = f" (Order ID: `{res.get('order_id')}`)" if res.get("order_id") else ""
            await interaction.message.edit(
                content=f"✅ Trade **{status_text}** by {interaction.user.mention}{order_info}",
                view=self
            )

        @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
        async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            from engine.trades import decide_trade
            await interaction.response.defer()
            await decide_trade(approved=False)
            
            for child in self.children:
                child.disabled = True
                
            await interaction.message.edit(
                content=f"❌ Trade **REJECTED** by {interaction.user.mention}",
                view=self
            )

        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ **Trade approval request expired.**", view=self)
                except Exception:
                    pass

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
                title="📊 Cripto-3DS Engine Status",
                color=0x50fa7b if state.is_active else 0xff5555
            )
            status_str = "ACTIVE 🟢" if state.is_active else "PAUSED ⏸"
            mode_str = "TESTNET 🧪" if state.testnet else "REAL 💰"
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
                title="💼 Portfolio Balance",
                color=0x8be9fd
            )
            embed.add_field(name="Total Net Worth", value=f"**${total_val:,.2f}**", inline=False)
            embed.add_field(name="Holdings", value="\n".join(holdings_lines[:15]), inline=False)
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="start", description="Start/Activate automated trading engine")
        async def cmd_start(interaction: discord.Interaction):
            from engine.state import state
            from engine.ws_manager import broadcast_state
            state.is_active = True
            await broadcast_state()
            await interaction.response.send_message("✅ Trading Engine **ACTIVATED**.")

        @self.tree.command(name="pause", description="Pause automated trading engine")
        async def cmd_pause(interaction: discord.Interaction):
            from engine.state import state
            from engine.ws_manager import broadcast_state
            state.is_active = False
            await broadcast_state()
            await interaction.response.send_message("⏸ Trading Engine **PAUSED**.")

        @self.tree.command(name="check", description="Force immediate market evaluation")
        async def cmd_check(interaction: discord.Interaction):
            from engine.state import state
            state.rsi_strategy.cooldowns.clear()
            state.dca_strategy.cooldowns.clear()
            state.tpsl_strategy.cooldowns.clear()
            await interaction.response.send_message("🔄 Cooldown timers cleared. Evaluating market now.")

        @self.tree.command(name="testbuy", description="Simulate a test trade approval card")
        async def cmd_testbuy(interaction: discord.Interaction):
            from engine.state import state
            from engine.risk_manager import risk_manager
            from engine.ws_manager import broadcast_state
            await interaction.response.defer()
            curr_price = state.prices.get("BTCUSDT", 0.0) or 64000.0
            state.pending_trade = {
                "id": int(time.time()),
                "action": "BUY",
                "pair": "BTCUSDT",
                "amount_usdt": risk_manager.max_trade_usdt,
                "price": curr_price,
                "reason": "Simulated Discord /testbuy",
                "created_at": time.time(),
                "timeout_sec": 600
            }
            await broadcast_state()
            await self.send_interactive_trade(state.pending_trade)
            await interaction.followup.send("🚨 Test trade approval card dispatched below.")

        @self.tree.command(name="chart", description="Generate a dark-theme candlestick chart with RSI and Bollinger Bands")
        @app_commands.describe(pair="Trading pair symbol (e.g. BTC, ETH, BTCUSDT)", interval="Candlestick interval (15m, 1h, 2h, 4h, 1d)")
        @app_commands.choices(interval=[
            app_commands.Choice(name="15 Minutes", value="15m"),
            app_commands.Choice(name="1 Hour", value="1h"),
            app_commands.Choice(name="2 Hours", value="2h"),
            app_commands.Choice(name="4 Hours", value="4h"),
            app_commands.Choice(name="1 Day", value="1d"),
        ])
        async def cmd_chart(interaction: discord.Interaction, pair: str = "BTCUSDT", interval: app_commands.Choice[str] = None):
            from engine.chart_generator import fetch_klines, generate_candlestick_chart
            await interaction.response.defer()
            clean_pair = pair.upper().strip()
            if not clean_pair.endswith("USDT"):
                clean_pair += "USDT"
            interval_val = interval.value if isinstance(interval, app_commands.Choice) else (interval or "1h")
            klines = await fetch_klines(clean_pair, interval=interval_val, limit=30)
            if not klines or len(klines) < 5:
                await interaction.followup.send(f"❌ Could not fetch candlestick data for `{clean_pair}`.")
                return
            buf = await generate_candlestick_chart(clean_pair, klines, interval=interval_val)
            file = discord.File(fp=buf, filename=f"{clean_pair}_{interval_val}.png")
            embed = discord.Embed(
                title=f"📈 {clean_pair} • {interval_val.upper()} Candlestick Chart",
                color=0x8be9fd
            )
            embed.set_image(url=f"attachment://{clean_pair}_{interval_val}.png")
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
                title="🤖 Gemini AI Market Analyst",
                description=answer[:4000],
                color=0xbd93f9
            )
            embed.set_footer(text=f"Model: {state.gemini_model} • Google AI Studio")
            await interaction.followup.send(f"**Q:** *{question}*", embed=embed)

        @self.tree.command(name="cleartrades", description="Purge unwanted trade records from database")
        @app_commands.describe(all_trades="Set true to wipe entire ledger, false to only clean rejected/test trades")
        async def cmd_cleartrades(interaction: discord.Interaction, all_trades: bool = False):
            from engine.db import clear_trade_history
            from engine.state import state
            from engine.ws_manager import broadcast_state
            await interaction.response.defer()
            deleted = await clear_trade_history(only_unexecuted=not all_trades, is_testnet=state.testnet)
            await broadcast_state()
            mode_str = "All Trades" if all_trades else "Rejected & Test Trades"
            await interaction.followup.send(f"🗑️ Cleaned **{deleted}** {mode_str} from database.")

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

    async def stop(self):
        self.ready_event.clear()
        self.is_connecting = False
        if self.client and not self.client.is_closed():
            try:
                await self.client.close()
            except Exception:
                pass
        if self.bot_task:
            self.bot_task.cancel()
            self.bot_task = None
        self.client = None

    async def send_interactive_trade(self, trade: dict) -> bool:
        if not HAS_DISCORD_PY:
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

            action = trade.get("action", "BUY")
            pair = trade.get("pair", "BTCUSDT")
            price = trade.get("price", 0.0)
            amount_usdt = trade.get("amount_usdt", 0.0)
            reason = trade.get("reason", "Strategy Signal")
            timeout_sec = trade.get("timeout_sec", 600)

            color = 0x50fa7b if action == "BUY" else 0xff5555
            embed = discord.Embed(
                title=f"🚨 Trade Confirmation Required: {action} {pair}",
                description=f"**Reason:** {reason}",
                color=color
            )
            embed.add_field(name="Price", value=f"${price:,.4f}", inline=True)
            embed.add_field(name="Amount", value=f"${amount_usdt:.2f} USDT", inline=True)
            embed.add_field(name="Auto-Expires", value=f"<t:{int(time.time() + timeout_sec)}:R>", inline=True)

            if state.gemini_api_key:
                from engine.ai_analyst import analyze_trade_signal
                from engine.strategies import calculate_bollinger_bands
                rsi_val = state.rsi_strategy.calculate_rsi(pair)
                hist = state.rsi_strategy.price_histories.get(pair, [])
                _, _, _, pct_b = calculate_bollinger_bands(hist, 20, 2.0)
                ai_note = await analyze_trade_signal(
                    pair=pair,
                    action=action,
                    price=price,
                    rsi=rsi_val,
                    pct_b=pct_b,
                    reason=reason,
                    price_history=hist,
                    api_key=state.gemini_api_key,
                    model=state.gemini_model
                )
                if ai_note:
                    embed.add_field(name="🤖 AI Risk Assessment", value=ai_note, inline=False)

            embed.set_footer(text="Cripto-3DS Engine • Tap button below to authorize")

            view = TradeApprovalView(trade_id=trade.get("id", int(time.time())), timeout=timeout_sec)
            msg = await channel.send(embed=embed, view=view)
            view.message = msg
            logger.info("Interactive Discord trade approval message sent with buttons.")
            return True
        except Exception as e:
            logger.error(f"Failed to send interactive Discord trade: {e}")
            return False

discord_bot_service = DiscordBotService()

async def send_discord_notification(subject: str, body: str, config: dict, trade: dict = None):
    # 1. Try interactive Discord Gateway bot first if trade payload provided
    if trade:
        sent = await discord_bot_service.send_interactive_trade(trade)
        if sent:
            return

    # 2. Fallback to webhook if configured
    webhook_url = config.get("discord_webhook_url")
    if not webhook_url:
        return
    
    payload = {
        "content": f"**{subject}**\n{body}"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status in (200, 204):
                    logger.info("Discord Webhook notification sent.")
                else:
                    logger.error(f"Discord Webhook notification failed. Status: {resp.status}")
    except Exception as e:
        logger.error(f"Error sending Discord Webhook: {e}")

