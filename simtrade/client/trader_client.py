"""SimTrade Python Client SDK for paper traders, bots, and quantitative researchers."""

import asyncio
import json
from typing import Any, Callable, Coroutine, Dict, List, Optional
import aiohttp
import logging

from simtrade.models.account import Account
from simtrade.models.market_data import Bar
from simtrade.models.order import Order, OrderCreate, OrderSide, OrderType, TimeInForce
from simtrade.models.trade import Trade

logger = logging.getLogger(__name__)


class SimTradeClient:
    """Asynchronous client library to connect, stream, and trade with SimTrade server."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", account_id: str = "trader_1"):
        self.base_url = base_url.rstrip("/")
        self.ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/unified"
        self.account_id = account_id

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._listen_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_bar_handlers: List[Callable[[Dict[str, Bar]], Coroutine[Any, Any, None]]] = []
        self._on_order_handlers: List[Callable[[Order], Coroutine[Any, Any, None]]] = []
        self._on_trade_handlers: List[Callable[[Trade], Coroutine[Any, Any, None]]] = []
        self._on_account_handlers: List[Callable[[Account], Coroutine[Any, Any, None]]] = []

    async def connect(self):
        """Establish HTTP session and WebSocket streaming connection."""
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self.ws_url)
        self._listen_task = asyncio.create_task(self._listen_ws())
        logger.info(f"Connected to SimTrade server at {self.base_url}")

    async def close(self):
        """Close connections cleanly."""
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("SimTrade client connection closed.")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    # Callback decorators
    def on_bar(self, handler: Callable[[Dict[str, Bar]], Coroutine[Any, Any, None]]):
        self._on_bar_handlers.append(handler)
        return handler

    def on_order(self, handler: Callable[[Order], Coroutine[Any, Any, None]]):
        self._on_order_handlers.append(handler)
        return handler

    def on_trade(self, handler: Callable[[Trade], Coroutine[Any, Any, None]]):
        self._on_trade_handlers.append(handler)
        return handler

    def on_account(self, handler: Callable[[Account], Coroutine[Any, Any, None]]):
        self._on_account_handlers.append(handler)
        return handler

    async def _listen_ws(self):
        """Listen for server messages over WebSocket."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    m_type = data.get("type")
                    m_data = data.get("data", {})

                    if m_type == "MARKET_BARS":
                        bars_dict = {
                            ticker: Bar(**b) for ticker, b in m_data.get("bars", {}).items()
                        }
                        for h in self._on_bar_handlers:
                            await h(bars_dict)

                    elif m_type == "ORDER_UPDATE":
                        order = Order(**m_data)
                        for h in self._on_order_handlers:
                            await h(order)

                    elif m_type == "TRADE_EXECUTION":
                        trade = Trade(**m_data)
                        for h in self._on_trade_handlers:
                            await h(trade)

                    elif m_type == "ACCOUNT_UPDATE":
                        acc = Account(**m_data)
                        for h in self._on_account_handlers:
                            await h(acc)

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"WebSocket listening exception: {e}")

    # Trading Methods
    async def submit_order(
        self,
        ticker: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        client_order_id: Optional[str] = None,
    ) -> Order:
        """Submit a new order via REST API."""
        payload = OrderCreate(
            ticker=ticker,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            client_order_id=client_order_id,
        ).model_dump(mode="json")

        async with self._session.post(
            f"{self.base_url}/api/v1/orders?account_id={self.account_id}",
            json=payload
        ) as resp:
            data = await resp.json()
            if resp.status != 201:
                raise ValueError(f"Order submission failed: {data.get('detail')}")
            return Order(**data)

    async def buy(self, ticker: str, quantity: float, order_type: OrderType = OrderType.MARKET, limit_price: Optional[float] = None) -> Order:
        return await self.submit_order(ticker=ticker, side=OrderSide.BUY, quantity=quantity, order_type=order_type, limit_price=limit_price)

    async def sell(self, ticker: str, quantity: float, order_type: OrderType = OrderType.MARKET, limit_price: Optional[float] = None) -> Order:
        return await self.submit_order(ticker=ticker, side=OrderSide.SELL, quantity=quantity, order_type=order_type, limit_price=limit_price)

    async def short(self, ticker: str, quantity: float, order_type: OrderType = OrderType.MARKET, limit_price: Optional[float] = None) -> Order:
        return await self.submit_order(ticker=ticker, side=OrderSide.SELL_SHORT, quantity=quantity, order_type=order_type, limit_price=limit_price)

    async def cancel(self, order_id: str) -> Order:
        """Cancel an open order."""
        async with self._session.delete(f"{self.base_url}/api/v1/orders/{order_id}?account_id={self.account_id}") as resp:
            data = await resp.json()
            if resp.status != 200:
                raise ValueError(f"Order cancellation failed: {data.get('detail')}")
            return Order(**data)

    async def get_account(self) -> Account:
        """Fetch current account ledger."""
        async with self._session.get(f"{self.base_url}/api/v1/account?account_id={self.account_id}") as resp:
            data = await resp.json()
            return Account(**data)

    async def get_active_orders(self) -> List[Order]:
        """Fetch current active orders."""
        async with self._session.get(f"{self.base_url}/api/v1/orders?account_id={self.account_id}") as resp:
            data = await resp.json()
            return [Order(**o) for o in data]

    async def get_performance(self) -> Dict[str, Any]:
        """Fetch analytics report (Sharpe, max drawdown, total return)."""
        async with self._session.get(f"{self.base_url}/api/v1/reports/performance?account_id={self.account_id}") as resp:
            return await resp.json()

    # Simulation Controls
    async def start_sim(self):
        async with self._session.post(f"{self.base_url}/api/v1/sim/start") as resp:
            return await resp.json()

    async def pause_sim(self):
        async with self._session.post(f"{self.base_url}/api/v1/sim/pause") as resp:
            return await resp.json()

    async def step_sim(self) -> Dict[str, Any]:
        async with self._session.post(f"{self.base_url}/api/v1/sim/step") as resp:
            return await resp.json()

    async def set_speed(self, speed: float):
        async with self._session.post(f"{self.base_url}/api/v1/sim/speed", json={"speed_multiplier": speed}) as resp:
            return await resp.json()
