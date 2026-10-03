"""In-process async event bus + realtime fan-out to dashboard websockets."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Callable, Iterable

Topic = str


class Event:
    __slots__ = ("topic", "data", "ts")

    def __init__(self, topic: Topic, data: dict[str, Any], ts: float | None = None):
        self.topic = topic
        self.data = data
        self.ts = ts if ts is not None else time.time()

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "ts": self.ts, "data": self.data}


class EventBus:
    """
    Fan-out bus. Subscribers get their own bounded queue so a slow consumer
    can never stall the trading loop (oldest events are dropped instead).
    """

    def __init__(self, history: int = 400, queue_size: int = 800):
        self._subs: list[asyncio.Queue] = []
        self._topic_subs: dict[Topic, list[Callable[[Event], Any]]] = {}
        self._history: deque[Event] = deque(maxlen=history)
        self._queue_size = queue_size
        self._seq = 0

    # ------------------------------------------------------------- publish
    def publish(self, topic: Topic, data: dict[str, Any] | None = None, **kw: Any) -> Event:
        payload = dict(data or {})
        payload.update(kw)
        ev = Event(topic, payload)
        self._seq += 1
        payload.setdefault("seq", self._seq)
        self._history.append(ev)
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()          # drop oldest, keep the stream live
                    q.put_nowait(ev)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        for cb in list(self._topic_subs.get(topic, ())):
            try:
                res = cb(ev)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception:
                pass
        return ev

    # ----------------------------------------------------------- subscribe
    def subscribe(self, topics: Iterable[Topic] | None = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    def on(self, topic: Topic, cb: Callable[[Event], Any]) -> None:
        self._topic_subs.setdefault(topic, []).append(cb)

    # -------------------------------------------------------------- history
    def recent(self, limit: int = 100, topic_prefix: str | None = None) -> list[dict]:
        items = list(self._history)
        if topic_prefix:
            items = [e for e in items if e.topic.startswith(topic_prefix)]
        return [e.to_dict() for e in items[-limit:]]


BUS = EventBus()
