"""Ожидание ввода текста от владельца (с тайм-аутом, чтобы случайный текст
через час не превратился в Д/з)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

TTL_SECONDS = 15 * 60  # по умолчанию; своё время — State.data["ttl"] (напр. уроки)


@dataclass
class State:
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.ts = time.monotonic()

    @property
    def ttl(self) -> float:
        value = self.data.get("ttl")
        return float(value) if isinstance(value, (int, float)) and value > 0 else float(TTL_SECONDS)

    def expired(self) -> bool:
        return time.monotonic() - self.ts > self.ttl


_states: dict[int, State] = {}


def start(user_id: int, name: str, **data: Any) -> State:
    state = State(name, dict(data))
    _states[user_id] = state
    return state


def get(user_id: int | None) -> State | None:
    """Активное состояние или None. Просроченное удаляется."""
    if user_id is None:
        return None
    state = _states.get(user_id)
    if state and state.expired():
        _states.pop(user_id, None)
        return None
    return state


def is_expired(user_id: int | None) -> bool:
    state = _states.get(user_id) if user_id is not None else None
    return bool(state and state.expired())


def clear(user_id: int | None) -> State | None:
    if user_id is None:
        return None
    return _states.pop(user_id, None)
