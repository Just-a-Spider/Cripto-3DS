import json
from typing import Set
from fastapi import WebSocket
from engine.state import state

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

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
        for connection in self.active_connections:
            try:
                await connection.send_text(msg_str)
            except Exception:
                to_remove.add(connection)
        for conn in to_remove:
            self.active_connections.discard(conn)

ws_manager = ConnectionManager()

async def broadcast_state():
    await ws_manager.broadcast(state.to_dict())
