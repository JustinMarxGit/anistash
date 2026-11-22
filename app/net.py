"""Shared HTTP clients and request merging.

One client per outside service keeps connections alive between calls, so each call
doesn't pay for a new TCP + TLS handshake. `once()` merges identical calls that arrive
while the first is still running, so they wait for its answer instead of repeating it.
"""

import asyncio

import httpx

_clients: dict[str, tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}
_running: dict[tuple, asyncio.Future] = {}


def client(name: str, timeout: float = 15) -> httpx.AsyncClient:
    """The shared client for `name` (a client belongs to the event loop that created it)."""
    loop = asyncio.get_running_loop()
    entry = _clients.get(name)
    if entry is None or entry[0] is not loop or entry[1].is_closed:
        entry = _clients[name] = (loop, httpx.AsyncClient(
            timeout=timeout, limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=120)))
    return entry[1]


async def close() -> None:
    loop = asyncio.get_running_loop()
    for name, (owner, c) in list(_clients.items()):
        if owner is loop:
            await c.aclose()
        del _clients[name]


async def once(key: tuple, make):
    """Runs make() for `key`, or waits for the run already in progress for the same key."""
    loop = asyncio.get_running_loop()
    running = _running.get(key)
    if running is not None and running.get_loop() is loop:
        return await asyncio.shield(running)
    task = loop.create_task(make())
    _running[key] = task

    def finished(t: asyncio.Task) -> None:
        if _running.get(key) is t:
            del _running[key]
        if not t.cancelled():
            t.exception()  # retrieved, so an error nobody waited for isn't logged as lost

    task.add_done_callback(finished)
    return await asyncio.shield(task)
