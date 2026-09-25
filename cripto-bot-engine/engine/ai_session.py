import logging
import time
from typing import Any, Dict, List, Optional

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from engine import ai_provider

logger = logging.getLogger("CriptoBotEngine")

MAX_MESSAGES_PER_SESSION = 20
SESSION_EXPIRATION_SECONDS = 86400  # 24 hours


class SessionEntry:
    def __init__(self, session_id: str):
        self.session_id: str = session_id
        self.history: InMemoryChatMessageHistory = InMemoryChatMessageHistory()
        self.last_active: float = time.time()
        self.created_at: float = time.time()

    def touch(self):
        self.last_active = time.time()

    def prune(self, max_messages: int = MAX_MESSAGES_PER_SESSION):
        msgs = self.history.messages
        if len(msgs) > max_messages:
            # Retain the most recent messages
            trimmed = msgs[-max_messages:]
            self.history.clear()
            for m in trimmed:
                self.history.add_message(m)


class SessionManager:
    """
    Manages multi-turn conversational chat sessions across Discord channels and Web Companion.
    """
    def __init__(self):
        self._sessions: dict[str, SessionEntry] = {}

    def get_or_create_session(self, session_id: str) -> SessionEntry:
        clean_id = (session_id or "default").strip()
        self.cleanup_expired()
        if clean_id not in self._sessions:
            self._sessions[clean_id] = SessionEntry(clean_id)
        entry = self._sessions[clean_id]
        entry.touch()
        return entry

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        clean_id = (session_id or "default").strip()
        if clean_id not in self._sessions:
            return []
        entry = self._sessions[clean_id]
        res = []
        for m in entry.history.messages:
            role = "user" if isinstance(m, HumanMessage) else ("assistant" if isinstance(m, AIMessage) else "system")
            res.append({"role": role, "content": ai_provider.extract_text_from_ai_message(m.content)})
        return res

    def clear_session(self, session_id: str) -> bool:
        clean_id = (session_id or "default").strip()
        if clean_id in self._sessions:
            self._sessions[clean_id].history.clear()
            del self._sessions[clean_id]
            logger.info(f"Cleared AI conversational session: {clean_id}")
            return True
        return False

    def list_active_sessions(self) -> list[dict[str, Any]]:
        self.cleanup_expired()
        res = []
        for sid, entry in self._sessions.items():
            res.append({
                "session_id": sid,
                "message_count": len(entry.history.messages),
                "created_at": entry.created_at,
                "last_active": entry.last_active
            })
        return res

    def cleanup_expired(self):
        now = time.time()
        expired = [
            sid for sid, entry in self._sessions.items()
            if (now - entry.last_active) > SESSION_EXPIRATION_SECONDS
        ]
        for sid in expired:
            del self._sessions[sid]
            logger.debug(f"Pruned expired AI session: {sid}")


# Global session manager instance
session_manager = SessionManager()


async def execute_chat_turn(
    query: str,
    session_id: str = "default",
    market_context: dict[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
    fallback_api_key: str | None = None,
) -> dict[str, Any]:
    """
    Executes a multi-turn chat interaction with conversation memory and live market context.
    """
    entry = session_manager.get_or_create_session(session_id)
    entry.prune()

    # Build market context summary for system message
    ctx = market_context or {}
    prices = ctx.get("prices", {})
    indicators = ctx.get("indicators", {})
    fav_pairs = ctx.get("favorite_pairs", list(prices.keys()))
    portfolio = ctx.get("portfolio", {})
    cost_bases = ctx.get("cost_bases", {})
    fng_str = ctx.get("fng_str", "50/100 (Neutral)")

    watchlist_lines = []
    for p in fav_pairs:
        pr = prices.get(p, 0.0)
        ind = indicators.get(p, {})
        rsi = ind.get("rsi", "N/A")
        pct_b = ind.get("pct_b", "N/A")
        watchlist_lines.append(f"- {p}: Price=${pr:,.4f}, Wilder RSI={rsi}, %B={pct_b}")
    watchlist_str = "\n".join(watchlist_lines) if watchlist_lines else "No active watchlist pairs."

    position_lines = []
    for asset, qty in portfolio.items():
        if asset != "USDT" and qty > 0:
            pair = f"{asset}USDT"
            curr_p = prices.get(pair, 0.0)
            cost_b = cost_bases.get(pair, 0.0)
            if curr_p > 0 and (qty * curr_p) >= 1.0:
                pnl_pct = ((curr_p - cost_b) / cost_b * 100.0) if cost_b > 0 else 0.0
                pnl_usd = (curr_p - cost_b) * qty if cost_b > 0 else 0.0
                position_lines.append(f"- {asset}: {qty:.6f} @ Entry ${cost_b:,.4f} | Current ${curr_p:,.4f} | PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
    positions_str = "\n".join(position_lines) if position_lines else "No open altcoin positions (100% USDT Reserve)."

    system_prompt = f"""You are Cripto-3DS AI Assistant, an expert quantitative cryptocurrency trading and portfolio analyst.
You maintain conversation context across turns. Provide concise, actionable, and mathematically grounded answers.

Live Market Context:
- Crypto Fear & Greed Index: {fng_str}
- USDT Balance: ${ctx.get('usdt_balance', 0.0):.2f} (Testnet: {ctx.get('testnet', True)})
- Open Positions:
{positions_str}
- Watchlist Technicals:
{watchlist_str}
"""

    primary_model = ai_provider.get_chat_model(
        provider=provider,
        model_name=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=800
    )

    fallback_model_obj = None
    if fallback_provider and (fallback_api_key or fallback_provider == "ollama"):
        fallback_model_obj = ai_provider.get_chat_model(
            provider=fallback_provider,
            model_name=fallback_model,
            api_key=fallback_api_key,
            temperature=0.3,
            max_tokens=800
        )

    if not primary_model and not fallback_model_obj:
        return {
            "answer": "[AI CONFIG ERROR] No valid AI provider credentials configured. Please set your API key in Web Companion Settings.",
            "session_id": session_id,
            "turn_count": len(entry.history.messages) // 2
        }

    executable = primary_model or fallback_model_obj
    if primary_model and fallback_model_obj and primary_model != fallback_model_obj:
        try:
            executable = primary_model.with_fallbacks([fallback_model_obj])
        except Exception:
            executable = primary_model

    # Construct conversation messages with sanitized history
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for m in entry.history.messages:
        clean_text = ai_provider.extract_text_from_ai_message(m.content)
        if isinstance(m, HumanMessage):
            messages.append(HumanMessage(content=clean_text))
        elif isinstance(m, AIMessage):
            messages.append(AIMessage(content=clean_text))
        else:
            messages.append(m)
    messages.append(HumanMessage(content=query))

    try:
        response = await executable.ainvoke(messages) # type: ignore
        answer = ai_provider.extract_text_from_ai_message(response)

        # Update history
        entry.history.add_user_message(query)
        entry.history.add_ai_message(answer)
        entry.touch()

        return {
            "answer": answer,
            "session_id": session_id,
            "turn_count": len(entry.history.messages) // 2
        }
    except Exception as e:
        logger.error(f"Chat turn execution error on session {session_id}: {e}")
        return {
            "answer": f"[AI OUTAGE] AI provider encountered an error: {e}. Please check your provider settings or try again.",
            "session_id": session_id,
            "turn_count": len(entry.history.messages) // 2
        }
