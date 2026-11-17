import pytest

from showwatch.chains.base import BaseChain
from showwatch.chains.registry import ChainRegistry, UnknownChain
from showwatch.domain.models import Capability, CapabilityNotSupported


class _WatchOnly(BaseChain):
    code = "demo"
    capabilities = Capability.WATCH


def test_chains_are_found_by_code() -> None:
    registry = ChainRegistry()
    chain = _WatchOnly()
    registry.register(chain)
    assert registry.get("demo") is chain
    assert registry.codes() == ["demo"]


def test_an_unknown_code_is_an_error() -> None:
    with pytest.raises(UnknownChain):
        ChainRegistry().get("nope")


def test_require_checks_the_capability() -> None:
    registry = ChainRegistry()
    registry.register(_WatchOnly())
    assert registry.require("demo", Capability.WATCH).code == "demo"
    with pytest.raises(CapabilityNotSupported, match="BOOK"):
        registry.require("demo", Capability.BOOK)


async def test_a_method_a_chain_does_not_provide_refuses_loudly() -> None:
    chain = _WatchOnly()
    with pytest.raises(CapabilityNotSupported):
        await chain.list_movies(None)  # type: ignore[arg-type]
    with pytest.raises(CapabilityNotSupported):
        await chain.list_showtimes(None, None, None)  # type: ignore[arg-type]
    with pytest.raises(CapabilityNotSupported):
        await chain.probe_signals(None)  # type: ignore[arg-type]
    with pytest.raises(CapabilityNotSupported):
        await chain.book(None, 2)  # type: ignore[arg-type]


def test_the_capability_table_matches_the_chain_classes() -> None:
    from showwatch.chains.bms.scraper import BookMyShowChain
    from showwatch.chains.capabilities import CAPABILITIES, can_search_showtimes
    from showwatch.chains.district.scraper import DistrictChain
    from showwatch.chains.harkins.scraper import HarkinsChain

    classes = {"harkins": HarkinsChain, "bms": BookMyShowChain, "district": DistrictChain}
    assert {code: cls.capabilities for code, cls in classes.items()} == CAPABILITIES
    assert can_search_showtimes("harkins")
    assert not can_search_showtimes("bms")
    assert not can_search_showtimes("unknown")
