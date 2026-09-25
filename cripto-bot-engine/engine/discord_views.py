import functools
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("CriptoBotEngine")

try:
    import discord
    from discord import app_commands
    HAS_DISCORD_PY = True
except ImportError:
    discord = None
    app_commands = None
    HAS_DISCORD_PY = False
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


def build_briefing_embed(data: dict, model_name: str) -> Any | None:
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


def build_multi_trade_embed(trades: list, resolutions: dict = None) -> Any | None:
    if not HAS_DISCORD_PY:
        return None
    resolutions = resolutions or {}
    total = len(trades)
    pending_count = sum(1 for t in trades if t.get("id") not in resolutions)
    approved_count = sum(1 for r in resolutions.values() if r.get("status") == "approved")
    rejected_count = sum(1 for r in resolutions.values() if r.get("status") in ("rejected", "blocked"))

    if pending_count == 0:
        if approved_count > 0 and rejected_count == 0:
            color = 0x50fa7b  # All Approved (Green)
            title = f"All Trades Processed ({approved_count}/{total} Approved)"
        elif rejected_count > 0 and approved_count == 0:
            color = 0xff5555  # All Rejected (Red)
            title = f"All Trades Rejected ({rejected_count}/{total} Rejected)"
        else:
            color = 0x8be9fd  # Mixed (Cyan)
            title = f"Batch Trades Resolved ({approved_count} Approved, {rejected_count} Rejected)"
    else:
        color = 0xbd93f9  # Active Purple
        title = f"Trade Confirmations Required ({pending_count}/{total} Pending)"

    embed = discord.Embed(
        title=title,
        color=color,
        description="**Multi-Asset Signal Pipeline • Table & Quick Actions**"
    )

    # Formatted ASCII/Markdown table
    table_lines = [
        "```",
        f"{'Pair':<9} {'Act':<4} {'Price':<10} {'Size':<6} {'Projected':<14} {'Risk':<5} {'Status':<9}",
        "─" * 63
    ]
    for t in trades:
        tid = t.get("id")
        pair = t.get("pair", "")[:8]
        act = t.get("action", "BUY")[:4]
        price_str = f"${t.get('price', 0.0):,.2f}"[:9]
        size_str = f"${t.get('amount_usdt', 0.0):.0f}"[:6]

        proj = t.get("projection") or {}
        if act == "SELL":
            pnl_u = proj.get("projected_pnl_usdt", 0.0)
            pnl_p = proj.get("projected_pnl_percent", 0.0)
            proj_str = f"${pnl_u:+.2f} ({pnl_p:+.0f}%)" if proj else "-"
        else:
            tp_u = proj.get("target_profit_usdt", 0.0)
            sl_u = proj.get("max_risk_usdt", 0.0)
            proj_str = f"+${tp_u:.1f}/-${sl_u:.1f}" if proj else "-"
        proj_str = proj_str[:13]

        risk_str = str(t.get("ai_risk", "MED")).split(" ")[0][:5]
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

        table_lines.append(f"{pair:<9} {act:<4} {price_str:<10} {size_str:<6} {proj_str:<14} {risk_str:<5} {status_str:<9}")
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
                oid = f" (Order: `{res.get('order_id')}`)" if res.get('order_id') else ""
                lines.append(f"**Status:** [APPROVED]{oid}")
            else:
                r_text = f" ({res.get('reason')})" if res.get('reason') else ""
                lines.append(f"**Status:** [REJECTED]{r_text}")
        else:
            lines.append(f"**Status:** [PENDING] • **Expires:** <t:{exp_ts}:R>")

        lines.append(f"**Reason:** {reason}")
        proj = t.get("projection")
        if proj and proj.get("summary_str"):
            lines.append(f"**Outcome Projection:** `{proj.get('summary_str')}`")
        if ai_verdict:
            sl_info = f" • **Suggested SL:** `-{ai_sl}%`" if ai_sl else ""
            lines.append(f"**AI Analysis:** `{ai_verdict}` • **Risk:** `{ai_risk}`{sl_info}")
            if ai_summary:
                lines.append(f"*{ai_summary[:180]}*")

        embed.add_field(name=field_name, value="\n".join(lines), inline=False)

    embed.set_footer(text="Cripto-3DS Engine • Use Quick Buttons or Select Dropdown Below")
    return embed


_BaseView = discord.ui.View if HAS_DISCORD_PY else object


class BatchTradeApprovalView(_BaseView):
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
        trade = {
            "id": trade_id,
            "pair": "TRADE",
            "action": "BUY",
            "price": 0.0,
            "amount_usdt": 0.0,
            "reason": "Trade Signal",
            "timeout_sec": timeout,
        }
        super().__init__(trades=[trade], timeout=timeout)
