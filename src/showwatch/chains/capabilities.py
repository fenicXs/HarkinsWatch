"""What each chain can do, readable without loading the chain (the bot has no browser code).

`tests/unit/test_chain_registry.py` checks this table against the chain classes, so it cannot
drift from them.
"""

from showwatch.domain.models import Capability

CAPABILITIES: dict[str, Capability] = {
    "harkins": Capability.WATCH | Capability.SHOWTIMES | Capability.BOOK,
    "bms": Capability.WATCH | Capability.SIGNALS,
    "district": Capability.WATCH | Capability.SIGNALS,
}


def can_search_showtimes(chain_code: str) -> bool:
    """Whether the chain lists sessions we can offer, as opposed to watching for them to open."""
    return Capability.SHOWTIMES in CAPABILITIES.get(chain_code, Capability(0))
