"""What every chain shares: default methods that refuse, and the "site pushed back" error."""

from datetime import date

from showwatch.domain.models import (
    BookingResult,
    Capability,
    CapabilityNotSupported,
    City,
    Movie,
    MovieListing,
    Showtime,
    ShowtimeListing,
    Signal,
    Theatre,
)


class BlockedError(Exception):
    """The site refused or challenged us (HTTP 403/429 or a challenge page). Back off; never evade."""


class RetryLater(Exception):
    """Not a failure and not an attempt: put the job back and run it again after `delay_s`.

    For example a browser profile that a crashed job left locked.
    """

    def __init__(self, reason: str, delay_s: int = 60) -> None:
        super().__init__(reason)
        self.delay_s = delay_s


class BaseChain:
    """Subclass this and override the methods the chain's `capabilities` promise.

    Anything not overridden raises `CapabilityNotSupported`, so a caller that forgets to check
    `capabilities` fails loudly instead of getting an empty answer.
    """

    code: str
    capabilities: Capability

    async def list_movies(self, city: City) -> list[MovieListing]:
        raise CapabilityNotSupported(f"{self.code} cannot list movies")

    async def list_showtimes(
        self, movie: Movie, theatre: Theatre, day: date
    ) -> list[ShowtimeListing]:
        raise CapabilityNotSupported(f"{self.code} cannot list showtimes")

    async def probe_signals(self, movie: Movie) -> list[Signal]:
        raise CapabilityNotSupported(f"{self.code} cannot report signals")

    async def book(self, showtime: Showtime, seats: int) -> BookingResult:
        raise CapabilityNotSupported(f"{self.code} cannot book seats")
