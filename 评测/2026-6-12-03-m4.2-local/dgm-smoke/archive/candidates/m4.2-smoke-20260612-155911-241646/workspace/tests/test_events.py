"""EventEmitter：订阅与顺序分发。"""
from __future__ import annotations

from mu.events import EventEmitter, RunFinished, RunStarted, TurnStarted


def test_emit_dispatches_to_all_subscribers_in_order():
    seen_a: list = []
    seen_b: list = []
    em = EventEmitter()
    em.subscribe(seen_a.append)
    em.subscribe(seen_b.append)

    e1 = RunStarted("task", "sid")
    e2 = TurnStarted(1)
    e3 = RunFinished("done")
    for e in (e1, e2, e3):
        em.emit(e)

    assert seen_a == [e1, e2, e3]
    assert seen_b == [e1, e2, e3]


def test_no_subscribers_is_silent():
    em = EventEmitter()
    em.emit(RunStarted("t", "s"))  # 不应抛错
