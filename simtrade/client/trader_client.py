"""SimTrade Python Client SDK for paper traders, bots, and quantitative researchers."""

import asyncio
from datetime import datetime
import json
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union
import aiohttp
import logging

from simtrade.models.account import Account
from simtrade.models.market_data import Bar
from simtrade.models.order import Order, OrderCreate, OrderSide, OrderType, TimeInForce
from simtrade.models.trade import Trade

logger = logging.getLogger(__name__)


class SimTradeClient:
    """Asynchronous client library to connect, stream, and trade with SimTrade server."""

    def __init__(self, base_url: str = "http://127.0.0.1:6688", account_id: str = "trader_1"):
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

    async def connect(self, with_ws: bool = True):
        """Establish HTTP session and optional WebSocket streaming connection."""
        self._session = aiohttp.ClientSession()
        if with_ws:
            try:
                self._ws = await self._session.ws_connect(self.ws_url)
                self._listen_task = asyncio.create_task(self._listen_ws())
                logger.info(f"Connected to SimTrade server at {self.base_url} (HTTP + WS)")
            except Exception as e:
                logger.warning(f"WebSocket connection failed ({e}), continuing HTTP-only")
        else:
            logger.info(f"Connected to SimTrade server at {self.base_url} (HTTP-only)")

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

    async def get_order(self, order_id: str) -> Order:
        """Fetch order details (active, filled, cancelled, or rejected) by order_id."""
        async with self._session.get(f"{self.base_url}/api/v1/orders/{order_id}") as resp:
            data = await resp.json()
            if resp.status != 200:
                raise ValueError(f"Failed to fetch order {order_id}: {data.get('detail')}")
            return Order(**data)

    async def get_trades(self, limit: int = 500) -> List[Trade]:
        """Fetch trade execution history for this account."""
        async with self._session.get(f"{self.base_url}/api/v1/trades?account_id={self.account_id}&limit={limit}") as resp:
            data = await resp.json()
            return [Trade(**t) for t in data]

    async def get_performance(self) -> Dict[str, Any]:
        """Fetch analytics report (Sharpe, max drawdown, total return)."""
        async with self._session.get(f"{self.base_url}/api/v1/reports/performance?account_id={self.account_id}") as resp:
            return await resp.json()

    # Simulation Controls & Metadata
    async def get_metadata(self) -> Dict[str, Any]:
        """Fetch simulation metadata (time range, total bars, available tickers, progress)."""
        async with self._session.get(f"{self.base_url}/api/v1/sim/metadata") as resp:
            return await resp.json()

    async def start_sim(self):
        """Start or resume continuous real-time playback (1.0x)."""
        async with self._session.post(f"{self.base_url}/api/v1/sim/start") as resp:
            return await resp.json()

    async def step_until(self, target_time: Union[str, datetime], account_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Advance simulation forward to target_time from current T_anchor.
        - If target_time <= current_time, server ignores request.
        - If liquidation triggers mid-way, server stops early and returns liquidation status.
        - If target_time is in a non-trading gap, returns empty bars with success status.
        """
        acc_id = account_id or self.account_id
        iso_str = target_time.isoformat() if isinstance(target_time, datetime) else str(target_time)
        payload = {"target_time": iso_str, "account_id": acc_id}
        async with self._session.post(f"{self.base_url}/api/v1/sim/step_until", json=payload) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise ValueError(f"Step until failed: {data.get('detail')}")
            return data

    async def reset_sim(self, start_time: Optional[str] = None) -> Dict[str, Any]:
        """Reset simulation playback cursor to start a fresh simulation pass."""
        payload = {"start_time": start_time} if start_time else {}
        async with self._session.post(f"{self.base_url}/api/v1/sim/reset", json=payload) as resp:
            return await resp.json()

    # Account Configuration & Pass Tagging
    async def setup_account(
        self,
        account_id: Optional[str] = None,
        tag: Optional[str] = None,
        initial_total_equity: float = 100_000.0,
        initial_cash: Optional[float] = None,
        initial_positions: Optional[Dict[str, float]] = None,
        leverage: Optional[float] = None,
        initial_margin_rate: Optional[float] = None,
        maintenance_margin_rate: Optional[float] = None,
    ) -> Account:
        """Negotiate / configure initial account terms, positions, and margin policy for this run pass."""
        acc_id = account_id or self.account_id
        payload = {
            "account_id": acc_id,
            "tag": tag or acc_id,
            "initial_total_equity": initial_total_equity,
            "initial_cash": initial_cash,
            "initial_positions": initial_positions,
            "leverage": leverage,
            "initial_margin_rate": initial_margin_rate,
            "maintenance_margin_rate": maintenance_margin_rate,
        }
        async with self._session.post(f"{self.base_url}/api/v1/account/setup", json=payload) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise ValueError(f"Account setup failed: {data.get('detail')}")
            self.account_id = acc_id
            return Account(**data)

    async def list_accounts(self) -> List[Dict[str, Any]]:
        """List all registered simulation pass accounts."""
        async with self._session.get(f"{self.base_url}/api/v1/accounts") as resp:
            return await resp.json()

    # Session Export & Persistence
    async def export_trades_csv(self, account_id: Optional[str] = None, save_path: Optional[str] = None) -> str:
        """Export trades as CSV string or save directly to a local file."""
        acc_id = account_id or self.account_id
        async with self._session.get(f"{self.base_url}/api/v1/reports/trades/csv?account_id={acc_id}") as resp:
            content = await resp.text()
            if save_path:
                with open(save_path, "w") as f:
                    f.write(content)
            return content

    async def save_session(self, account_id: Optional[str] = None, output_dir: str = "reports") -> Dict[str, Any]:
        """Trigger server to save trades, ledger, and performance reports to disk."""
        acc_id = account_id or self.account_id
        payload = {"account_id": acc_id, "output_dir": output_dir}
        async with self._session.post(f"{self.base_url}/api/v1/reports/save", json=payload) as resp:
            return await resp.json()

    async def list_passes(self) -> List[Dict[str, Any]]:
        """List all finished simulation passes cataloged on the server."""
        async with self._session.get(f"{self.base_url}/api/v1/passes") as resp:
            return await resp.json()

    async def get_pass(self, pass_id: str) -> Dict[str, Any]:
        """Fetch complete historical record of a finished simulation pass."""
        async with self._session.get(f"{self.base_url}/api/v1/passes/{pass_id}") as resp:
            if resp.status != 200:
                data = await resp.json()
                raise ValueError(f"Failed to fetch pass {pass_id}: {data.get('detail')}")
            return await resp.json()


# Alias for convenience
TraderClient = SimTradeClient
