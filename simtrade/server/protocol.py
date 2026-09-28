"""Protocol definitions and WebSocket message envelopes."""

from datetime import datetime
from simtrade.utils import utc_now
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class WSMessageType(str, Enum):
    # Inbound Client Messages
    SUBMIT_ORDER = "SUBMIT_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"
    SIM_CONTROL = "SIM_CONTROL"
    STEP_UNTIL = "STEP_UNTIL"
    SUBSCRIBE = "SUBSCRIBE"
    PING = "PING"

    # Outbound Server Messages
    MARKET_BARS = "MARKET_BARS"
    ORDER_UPDATE = "ORDER_UPDATE"
    TRADE_EXECUTION = "TRADE_EXECUTION"
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"
    MARGIN_CALL = "MARGIN_CALL"
    SIM_STATUS = "SIM_STATUS"
    STEP_UNTIL_RESULT = "STEP_UNTIL_RESULT"
    ERROR = "ERROR"
    PONG = "PONG"


class WSMessage(BaseModel):
    """Standard WebSocket message envelope."""
    type: WSMessageType
    timestamp: datetime = Field(default_factory=utc_now)
    data: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None
