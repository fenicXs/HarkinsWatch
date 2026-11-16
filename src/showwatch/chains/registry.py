"""Look chains up by code."""

from showwatch.domain.models import Capability, CapabilityNotSupported, Chain


class UnknownChain(KeyError):
    """No chain is registered under this code."""


class ChainRegistry:
    def __init__(self) -> None:
        self._chains: dict[str, Chain] = {}

    def register(self, chain: Chain) -> None:
        self._chains[chain.code] = chain

    def get(self, code: str) -> Chain:
        try:
            return self._chains[code]
        except KeyError:
            raise UnknownChain(code) from None

    def require(self, code: str, capability: Capability) -> Chain:
        """The chain, if it can do `capability`; otherwise `CapabilityNotSupported`."""
        chain = self.get(code)
        if capability not in chain.capabilities:
            raise CapabilityNotSupported(f"{code} cannot do {capability.name}")
        return chain

    def codes(self) -> list[str]:
        return sorted(self._chains)
