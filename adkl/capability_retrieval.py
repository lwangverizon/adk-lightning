"""Optional shared shortlisting for Unified Capability Selection.

Ranking and business pins are host inputs; bindings merge lifecycle retention.
Execution and authorization remain separate. No model router, embedding provider
or native object mutation is installed here.
"""
from collections import OrderedDict
import asyncio
import inspect
from typing import Any

from adkl.capability_contracts import (
    CapabilityCatalog,
    CapabilityNames,
    CapabilityRanker,
    CapabilityRetention,
    CapabilitySelection,
)


class CapabilityRetrieval:
    """One ranking per invocation and eligible catalog, shared by all toolsets.

    rank(context, catalog) returns CapabilitySelection. retain(context, catalog)
    optionally supplies live continuation names, re-evaluated on every access.
    Only completed results are bounded/cached; failed or cancelled work is not.
    A waiter cancellation does not cancel another caller's retrieval.
    Ranking never activates capabilities. Bindings own lifecycle composition.
    """

    def __init__(
        self,
        rank: CapabilityRanker,
        *,
        retain: CapabilityRetention | None = None,
        cache_size: int = 128,
    ) -> None:
        if not callable(rank) or (retain is not None and not callable(retain)):
            raise TypeError("rank and retain must be callable")
        if type(cache_size) is not int or cache_size < 1:
            raise ValueError("cache_size must be positive")
        self.rank, self.retain = rank, retain
        self.cache_size = cache_size
        self._cache = OrderedDict()
        self._locks = {}

    @staticmethod
    async def _resolve(value):
        return await value if inspect.isawaitable(value) else value

    @staticmethod
    def _validate(names: CapabilityNames, catalog: CapabilityCatalog) -> tuple[str, ...]:
        if not isinstance(names, (tuple, list, set, frozenset)) or any(
                not isinstance(name, str) for name in names):
            raise TypeError("Selection must contain capability names")
        if set(names) - {d.name for d in catalog}:
            raise ValueError("Selection contains an ineligible or unknown capability")
        return tuple(dict.fromkeys(names))

    async def select(
        self, context: Any, catalog: CapabilityCatalog
    ) -> CapabilitySelection:
        catalog = tuple(catalog)
        session = context.session
        key = (session.app_name, session.user_id, session.id,
               context.agent_name, context.invocation_id, catalog)
        # Lock objects are removed only after the last holder/waiter leaves.
        lock, users = self._locks.get(key, (asyncio.Lock(), 0))
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                result = self._cache.get(key)
                if result is None:
                    result = await self._resolve(self.rank(context, catalog))
                    if not isinstance(result, CapabilitySelection):
                        raise TypeError("rank must return CapabilitySelection")
                    if not isinstance(result.fallback, bool):
                        raise TypeError("fallback must be bool")
                    result = CapabilitySelection(self._validate(result.names, catalog),
                                                 result.fallback)
                    self._cache[key] = result
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        finally:
            _, users = self._locks[key]
            if users == 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)
        retained = () if self.retain is None else await self._resolve(self.retain(context, catalog))
        return CapabilitySelection(tuple(dict.fromkeys(
            (*result.names, *self._validate(retained, catalog)))), result.fallback)
