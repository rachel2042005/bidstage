"""Poll loop around `run_once`.

Seam: `run_forever`. The queue, the cycle, and `sleep` are fakes.
No SQL, Chroma, or model client is opened.

T-AG-36 — two queued bids run back to back; one sleep follows the empty claim.
T-AG-37 — an empty queue sleeps for the poll interval.
T-AG-38 — a stop flag set during the first bid leaves the next bid unclaimed.
"""

from __future__ import annotations

from agent.worker import run_forever

_INTERVAL = 5
_UNUSED = object()


class _Flag:
    def __init__(self) -> None:
        self.set_flag = False

    def __call__(self) -> bool:
        return self.set_flag

    def set(self) -> None:
        self.set_flag = True


class _Queue:
    def __init__(self, bids: list[str]) -> None:
        self._bids = list(bids)
        self.claimed: list[str] = []

    def claim(self) -> str | None:
        if not self._bids:
            return None
        bid = self._bids.pop(0)
        self.claimed.append(bid)
        return bid


def _cycle(queue: _Queue, **_kwargs: object) -> int:
    return 0 if queue.claim() is None else 1


def _forever(queue: _Queue, sleep, stop: _Flag, cycle=_cycle) -> None:
    run_forever(
        queue,
        interval=_INTERVAL,
        sleep=sleep,
        stop=stop,
        requirements=_UNUSED,
        rubrics=_UNUSED,
        search=_UNUSED,
        web=_UNUSED,
        venue_model=_UNUSED,
        content_model=_UNUSED,
        experience_model=_UNUSED,
        host_model=_UNUSED,
        music_model=_UNUSED,
        submit=_UNUSED,
        complete=_UNUSED,
        cycle=cycle,
    )


def test_backlog_drains_before_the_idle_sleep() -> None:
    """T-AG-36. Two bids, then an empty claim. Sleep is the idle step only."""
    queue = _Queue(["bid-a", "bid-b"])
    stop = _Flag()
    trace: list[object] = []

    def cycle(queue: _Queue, **_kwargs: object) -> int:
        trace.append("cycle")
        return _cycle(queue)

    def sleep(seconds: float) -> None:
        trace.append(("sleep", seconds))
        stop.set()

    _forever(queue, sleep, stop, cycle)

    assert queue.claimed == ["bid-a", "bid-b"]
    assert trace == ["cycle", "cycle", "cycle", ("sleep", _INTERVAL)]


def test_idle_queue_sleeps_for_the_poll_interval() -> None:
    """T-AG-37. Nothing is queued. The first sleep receives 5 seconds."""
    queue = _Queue([])
    stop = _Flag()
    sleeps: list[float] = []
    entered = {"cycle": 0}

    def cycle(queue: _Queue, **_kwargs: object) -> int:
        entered["cycle"] += 1
        assert queue.claim() is None
        return 0

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        stop.set()

    _forever(queue, sleep, stop, cycle)

    assert entered["cycle"] == 1
    assert sleeps == [_INTERVAL]


def test_stop_during_the_first_bid_does_not_claim_the_next() -> None:
    """T-AG-38. The in-flight bid finishes. The queued bid stays unclaimed."""
    queue = _Queue(["bid-a", "bid-b"])
    stop = _Flag()

    def cycle(queue: _Queue, **_kwargs: object) -> int:
        claimed = queue.claim()
        assert claimed == "bid-a"
        stop.set()
        return 1

    def sleep(_seconds: float) -> None:
        raise AssertionError("sleep")

    _forever(queue, sleep, stop, cycle)

    assert queue.claimed == ["bid-a"]
    assert queue._bids == ["bid-b"]
