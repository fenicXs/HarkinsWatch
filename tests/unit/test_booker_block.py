"""A 403/429 or challenge page on the seat page is a block, not "no seats"."""

import pytest

from showwatch.chains.base import BlockedError
from showwatch.chains.harkins import booker


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status


class _Page:
    def __init__(self, status: int, title: str) -> None:
        self._status, self._title = status, title

    async def goto(self, url: str) -> _Response:
        return _Response(self._status)

    async def title(self) -> str:
        return self._title


@pytest.mark.parametrize(
    ("status", "title"),
    [(429, "Harkins"), (403, "Harkins"), (200, "Attention Required! | Cloudflare")],
)
async def test_a_blocked_seat_page_raises_so_the_chain_backs_off(status: int, title: str) -> None:
    with pytest.raises(BlockedError):
        await booker._open_seat_map(_Page(status, title), "https://x.test/s/1")  # type: ignore[arg-type]
