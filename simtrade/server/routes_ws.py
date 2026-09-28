"""WebSocket endpoints for real-time market data broadcasting and bot communication."""

import json
from typing import Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import logging

from simtrade.models.order import OrderCreate
from simtrade.server.protocol import WSMessage, WSMessageType

logger = logging.getLogger(__name__)

ws_router = APIRouter()

# Active client connections
active_connections: Set[WebSocket] = set()


async def broadcast_ws(event_type: str, data: dict):
    """Broadcast JSON message to all connected WebSocket clients."""
    if not active_connections:
        return

    message = {
        "type": event_type,
        "data": data,
    }
    encoded = json.dumps(message)
    dead_connections = set()
    for ws in list(active_connections):
        try:
            await ws.send_text(encoded)
        except Exception:
            dead_connections.add(ws)

    for dead in dead_connections:
        active_connections.discard(dead)


@ws_router.websocket("/ws/unified")
async def websocket_unified_endpoint(websocket: WebSocket):
    """Unified bidirectional WebSocket channel for bots and the web dashboard."""
    from simtrade.server import routes_api

    await websocket.accept()
    active_connections.add(websocket)
    logger.info(f"WebSocket client connected. Total clients: {len(active_connections)}")

    sim = routes_api.simulator
    if sim:
        # Send initial status
        await websocket.send_json({
            "type": "SIM_STATUS",
            "data": {
                "current_time": sim.clock.current_time.isoformat(),
                "is_running": sim.clock.is_running,
                "is_paused": sim.clock.is_paused,
                "speed_multiplier": sim.clock.speed_multiplier,
            }
        })
        # Send current account state
        default_acc = sim.account_mgr.get_or_create_account(sim.account_mgr.default_account_id)
        await websocket.send_json({
            "type": "ACCOUNT_UPDATE",
            "data": default_acc.model_dump(mode="json")
        })

    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg_dict = json.loads(text)
                msg_type = msg_dict.get("type")
                data = msg_dict.get("data", {})

                if msg_type == "SUBMIT_ORDER":
                    account_id = data.get("account_id", "trader_1")
                    order_in = OrderCreate(**data)
                    order = sim.submit_order(order_in, account_id=account_id)
                    await websocket.send_json({
                        "type": "ORDER_UPDATE",
                        "data": order.model_dump(mode="json"),
                    })

                elif msg_type == "CANCEL_ORDER":
                    account_id = data.get("account_id", "trader_1")
                    order_id = data.get("order_id")
                    order = sim.cancel_order(order_id, account_id=account_id)
                    if order:
                        await websocket.send_json({
                            "type": "ORDER_UPDATE",
                            "data": order.model_dump(mode="json"),
                        })

                elif msg_type == "SIM_CONTROL":
                    action = data.get("action")
                    if action == "start":
                        sim.start()
                    elif action == "pause":
                        sim.pause()
                    elif action == "step":
                        await sim.step()
                    elif action == "speed":
                        sim.clock.set_speed(data.get("speed", 1.0))

                elif msg_type == "STEP_UNTIL":
                    account_id = data.get("account_id", "trader_1")
                    target_time_str = data.get("target_time")
                    if target_time_str:
                        from datetime import datetime
                        target_dt = datetime.fromisoformat(target_time_str)
                        res = await sim.step_until(target_dt, account_id=account_id)
                        await websocket.send_json({
                            "type": "STEP_UNTIL_RESULT",
                            "data": res,
                        })

                elif msg_type == "PING":
                    await websocket.send_json({"type": "PONG", "data": {}})

            except Exception as e:
                logger.error(f"Error handling WS message: {e}")
                await websocket.send_json({
                    "type": "ERROR",
                    "data": {"error": str(e)}
                })

    except WebSocketDisconnect:
        active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Remaining clients: {len(active_connections)}")
