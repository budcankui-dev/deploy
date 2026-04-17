from __future__ import annotations

import socket
from typing import Any

import uvicorn


_DUALSTACK_HOSTS = {"", "::", "::0", "[::]", "0::0"}


def run_uvicorn(app: Any, host: str, port: int, **kwargs: Any) -> None:
    """Run uvicorn, forcing IPv4+IPv6 dual-stack when host is ``::``.

    asyncio/uvicorn 在绑定 ``::`` 时不会主动清 ``IPV6_V6ONLY``，在
    ``net.ipv6.bindv6only`` 默认为 0 的内核上会被当成 v6-only，
    导致 IPv4 访问被 Connection refused。
    这里在 host 为 ``::`` 时手动创建 socket 并显式 ``IPV6_V6ONLY=0``，
    再通过 ``fd=`` 参数交给 uvicorn 使用。
    其他 host（含 ``0.0.0.0`` 或具体地址）走原生 ``uvicorn.run``。
    """
    normalized = (host or "").strip().strip("[]")
    if normalized not in _DUALSTACK_HOSTS:
        uvicorn.run(app, host=host, port=port, **kwargs)
        return

    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    except (OSError, AttributeError):
        pass
    sock.bind(("::", int(port)))
    sock.listen(2048)
    uvicorn.run(app, fd=sock.fileno(), **kwargs)
