import showwatch
from showwatch.domain.models import Capability, CapabilityNotSupported


def test_package_has_version() -> None:
    assert showwatch.__version__ == "0.1.0"


def test_capabilities_combine_as_flags() -> None:
    harkins = Capability.WATCH | Capability.SHOWTIMES | Capability.BOOK
    assert Capability.BOOK in harkins
    assert Capability.SIGNALS not in harkins


def test_capability_not_supported_is_an_exception() -> None:
    assert issubclass(CapabilityNotSupported, Exception)
