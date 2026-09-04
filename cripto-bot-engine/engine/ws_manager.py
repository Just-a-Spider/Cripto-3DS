import asyncio
import json
import time
from typing import Set, Optional
from fastapi import WebSocket
from engine.state import state

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._last_broadcast_time: float = 0.0
        self._throttle_interval: float = 0.25  # 250ms = max 4Hz
        self._pending_broadcast: bool = False
        self._broadcast_task: Optional[asyncio.Task] = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        msg_str = json.dumps(message)
        to_remove = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_text(msg_str)
            except Exception:
                to_remove.add(connection)
        for conn in to_remove:
            self.active_connections.discard(conn)

    async def trigger_debounced_broadcast(self):
        if not self.active_connections:
            return

        now = time.time()
        time_since_last = now - self._last_broadcast_time

        if time_since_last >= self._throttle_interval:
            self._last_broadcast_time = now
            await self.broadcast(state.to_dict())
        else:
            if not self._pending_broadcast:
                self._pending_broadcast = True
                async def _delayed_broadcast():
                    delay = max(0.05, self._throttle_interval - (time.time() - self._last_broadcast_time))
                    await asyncio.sleep(delay)
                    self._pending_broadcast = False
                    self._last_broadcast_time = time.time()
                    await self.broadcast(state.to_dict())
                self._broadcast_task = asyncio.create_task(_delayed_broadcast())

ws_manager = ConnectionManager()

async def broadcast_state(force_immediate: bool = False):
    if force_immediate:
        await ws_manager.broadcast(state.to_dict())
    else:
        await ws_manager.trigger_debounced_broadcast()

