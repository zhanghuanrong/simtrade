"""Order matching engine with realistic OHLCV price execution, slippage, and volume caps."""

from datetime import datetime
from simtrade.utils import utc_now
from typing import Dict, List, Optional, Tuple
import logging

from simtrade.config import MatchingConfig
from simtrade.models.market_data import Bar
from simtrade.models.order import Order, OrderSide, OrderStatus, OrderType, TimeInForce
from simtrade.models.trade import Trade

logger = logging.getLogger(__name__)


class MatchingEngine:
    """Matches active orders against incoming bar data."""

    def __init__(self, config: Optional[MatchingConfig] = None):
        self.config = config or MatchingConfig()
        # Active orders waiting for fills: order_id -> Order
        self.active_orders: Dict[str, Order] = {}

    def add_order(self, order: Order) -> Order:
        """Register an accepted order in the order book."""
        order.status = OrderStatus.ACCEPTED
        order.updated_at = utc_now()
        self.active_orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> Optional[Order]:
        """Cancel an active order."""
        if order_id in self.active_orders:
            order = self.active_orders.pop(order_id)
            order.status = OrderStatus.CANCELLED
            order.updated_at = utc_now()
            return order
        return None

    def calculate_commission(self, quantity: float, price: float) -> float:
        comm = quantity * self.config.commission_per_share
        return round(max(self.config.min_commission, comm), 4)

    def calculate_slippage_price(self, base_price: float, side: OrderSide) -> float:
        slippage_fraction = self.config.slippage_bps / 10_000.0
        if side == OrderSide.BUY:
            return round(base_price * (1.0 + slippage_fraction), self.config.price_precision)
        else:
            return round(base_price * (1.0 - slippage_fraction), self.config.price_precision)

    def match_bar(self, bar: Bar) -> List[Tuple[Order, Trade]]:
        """Match pending active orders for the given ticker against the 1m bar."""
        executions: List[Tuple[Order, Trade]] = []
        orders_to_check = [
            order for order in self.active_orders.values()
            if order.ticker == bar.ticker
        ]

        # Available execution volume in this bar (e.g. 10% of total volume)
        remaining_bar_volume = bar.volume * self.config.max_volume_share

        for order in orders_to_check:
            if remaining_bar_volume <= 0 and bar.volume > 0:
                break

            fill_price: Optional[float] = None
            should_fill = False

            # 1. MARKET ORDER
            if order.order_type == OrderType.MARKET:
                should_fill = True
                raw_fill = self.calculate_slippage_price(bar.open, order.side)
                # Clamp fill price within [bar.low, bar.high]
                fill_price = max(bar.low, min(bar.high, raw_fill))

            # 2. LIMIT ORDER
            elif order.order_type == OrderType.LIMIT:
                if order.limit_price is None:
                    continue

                if order.side == OrderSide.BUY:
                    # Buy Limit fills if bar traded down to or below limit_price
                    if bar.low <= order.limit_price:
                        should_fill = True
                        fill_price = min(order.limit_price, bar.open)
                else: # SELL or SELL_SHORT
                    # Sell Limit fills if bar traded up to or above limit_price
                    if bar.high >= order.limit_price:
                        should_fill = True
                        fill_price = max(order.limit_price, bar.open)

            # 3. STOP ORDER
            elif order.order_type == OrderType.STOP:
                if order.stop_price is None:
                    continue

                if order.side == OrderSide.BUY:
                    if bar.high >= order.stop_price:
                        should_fill = True
                        fill_price = max(order.stop_price, bar.open)
                else:
                    if bar.low <= order.stop_price:
                        should_fill = True
                        fill_price = min(order.stop_price, bar.open)

            # 4. STOP_LIMIT ORDER
            elif order.order_type == OrderType.STOP_LIMIT:
                if order.stop_price is None or order.limit_price is None:
                    continue

                triggered = False
                if order.side == OrderSide.BUY and bar.high >= order.stop_price:
                    triggered = True
                elif order.side in (OrderSide.SELL, OrderSide.SELL_SHORT) and bar.low <= order.stop_price:
                    triggered = True

                if triggered:
                    # Once triggered, evaluate limit
                    if order.side == OrderSide.BUY and bar.low <= order.limit_price:
                        should_fill = True
                        fill_price = min(order.limit_price, bar.open)
                    elif order.side in (OrderSide.SELL, OrderSide.SELL_SHORT) and bar.high >= order.limit_price:
                        should_fill = True
                        fill_price = max(order.limit_price, bar.open)

            if should_fill and fill_price is not None:
                # Determine executable quantity considering volume participation
                qty_to_fill = order.remaining_quantity
                if remaining_bar_volume > 0 and qty_to_fill > remaining_bar_volume:
                    # Handle TimeInForce for partial fills
                    if order.time_in_force == TimeInForce.FOK:
                        # FOK cannot be filled partially, cancel
                        self.active_orders.pop(order.order_id, None)
                        order.status = OrderStatus.CANCELLED
                        order.reject_reason = "FOK order cannot be completely filled by bar volume"
                        continue
                    elif order.time_in_force == TimeInForce.IOC:
                        qty_to_fill = remaining_bar_volume
                        # IOC cancels remaining after partial fill
                    else:
                        qty_to_fill = remaining_bar_volume
                
                if qty_to_fill <= 0:
                    continue

                remaining_bar_volume -= qty_to_fill
                commission = self.calculate_commission(qty_to_fill, fill_price)
                
                trade = Trade(
                    order_id=order.order_id,
                    account_id=order.account_id,
                    ticker=order.ticker,
                    side=order.side,
                    price=round(fill_price, self.config.price_precision),
                    quantity=qty_to_fill,
                    commission=commission,
                    timestamp=bar.timestamp,
                )

                # Update order state
                new_filled = order.filled_quantity + qty_to_fill
                order.avg_fill_price = round(
                    ((order.avg_fill_price * order.filled_quantity) + (fill_price * qty_to_fill)) / new_filled,
                    self.config.price_precision
                )
                order.filled_quantity = new_filled
                order.remaining_quantity -= qty_to_fill
                order.updated_at = utc_now()

                if order.remaining_quantity <= 0:
                    order.status = OrderStatus.FILLED
                    self.active_orders.pop(order.order_id, None)
                else:
                    if order.time_in_force == TimeInForce.IOC:
                        order.status = OrderStatus.CANCELLED
                        self.active_orders.pop(order.order_id, None)
                    else:
                        order.status = OrderStatus.PARTIALLY_FILLED

                executions.append((order, trade))

        return executions
