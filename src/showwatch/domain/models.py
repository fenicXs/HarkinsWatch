"""Domain types, the Chain Protocol and the capability flags. No I/O lives here."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Flag, auto
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

MovieStatus = Literal["now_showing", "coming_soon"]
UserStatus = Literal["pending", "approved", "denied"]
WatchStage = Literal["waiting", "listed", "dates", "venues", "sessions", "done"]


class CapabilityNotSupported(Exception):
    """Raised when a chain is asked to do something its capabilities do not include."""


class Capability(Flag):
    WATCH = auto()  # list movies / coming-soon for a city
    SHOWTIMES = auto()  # list sessions for a movie and date
    SIGNALS = auto()  # report pre-opening signals for insider detection
    BOOK = auto()  # hold seats and return a payment link


# --- What a chain returns (frozen dataclasses) -------------------------------------------------


@dataclass(frozen=True)
class MovieListing:
    external_id: str
    title: str
    url: str
    status: MovieStatus


@dataclass(frozen=True)
class ShowtimeListing:
    external_session_id: str
    theatre_external_id: str
    starts_at: datetime  # timezone-aware
    format: str
    booking_url: str


@dataclass(frozen=True)
class Signal:
    stage: Literal["listed", "dates", "venues", "sessions"]
    evidence: dict[str, object]


@dataclass(frozen=True)
class BookingResult:
    kind: Literal["held", "manual"]
    seats: list[str]
    url: str


# --- Rows as the rest of the app sees them (mirror the tables in Appendix A) ---------------------


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class City(_Row):
    id: int
    code: str
    name: str
    tz: str


class Theatre(_Row):
    id: int
    chain_id: int
    city_id: int
    external_id: str
    name: str


class Movie(_Row):
    id: int
    chain_id: int
    city_id: int
    external_id: str
    title: str
    normalized_title: str
    url: str
    status: MovieStatus
    first_seen_at: datetime
    last_seen_at: datetime
    released_at: datetime | None


class Showtime(_Row):
    id: int
    movie_id: int
    theatre_id: int
    external_session_id: str
    starts_at: datetime
    format: str
    booking_url: str
    updated_at: datetime


class WatchItem(_Row):
    id: int
    user_id: int
    city_id: int
    query: str
    movie_id: int | None
    stage: WatchStage
    fast_until: datetime | None
    format_pref: str | None


class User(_Row):
    id: int
    telegram_user_id: int
    chat_id: int | None
    display_name: str | None
    status: UserStatus
    is_admin: bool
    pending_notified: bool
    current_city_id: int | None


JobStatus = Literal["queued", "running", "done", "failed"]
SnapshotStatus = Literal["good", "suspect"]


class ChainInfo(_Row):
    """The `chains` table row. (`Chain` below is the plug-in interface, not this.)"""

    id: int
    code: str
    enabled: bool
    blocked_until: datetime | None


class ChainSnapshot(_Row):
    id: int
    chain_id: int
    city_id: int
    taken_at: datetime
    movie_count: int
    status: SnapshotStatus


class Job(_Row):
    id: int
    kind: str
    payload: dict[str, Any]
    status: JobStatus
    run_after: datetime
    attempts: int
    max_attempts: int
    chain_id: int | None
    city_id: int | None
    locked_by: str | None
    locked_at: datetime | None
    last_error: str | None
    result: dict[str, Any] | None


class Chain(Protocol):
    """A theatre chain or ticketing site. `capabilities` says which methods may be called."""

    code: str
    capabilities: Capability

    async def list_movies(self, city: City) -> list[MovieListing]: ...

    async def list_showtimes(
        self, movie: Movie, theatre: Theatre, day: date
    ) -> list[ShowtimeListing]: ...

    async def probe_signals(self, movie: Movie) -> list[Signal]: ...

    async def book(self, showtime: Showtime, seats: int) -> BookingResult: ...
