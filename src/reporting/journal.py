"""Signal log (spec section 27) and trade journal (spec section 28), persisted as
JSON Lines so every field survives without needing a fixed CSV header up front."""

from __future__ import annotations

import json
import os
from typing import List, Optional

from models.signal import Signal
from models.trade import Trade


class SignalJournal:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._buffer: List[Signal] = []

    def log(self, signal: Signal) -> None:
        self._buffer.append(signal)
        with open(self.path, "a") as f:
            f.write(json.dumps(signal.as_log_row()) + "\n")

    @property
    def signals(self) -> List[Signal]:
        return list(self._buffer)


class TradeJournal:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._buffer: List[Trade] = []

    def record(self, trade: Trade) -> None:
        self._buffer.append(trade)
        with open(self.path, "a") as f:
            f.write(json.dumps(trade.as_log_row()) + "\n")

    @property
    def trades(self) -> List[Trade]:
        return list(self._buffer)

    def flush(self) -> None:
        # Records are written as they're appended; flush exists so callers have an
        # explicit end-of-day checkpoint to call without needing to know that.
        pass
