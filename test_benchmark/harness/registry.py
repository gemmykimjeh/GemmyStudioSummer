"""A tiny registry so new agents/benchmarks plug in without touching the runner.

Adding a new adapter is a two-step, zero-core-edit operation:

1. Implement ``Agent`` or ``Benchmark`` in ``harness/agents`` or
   ``harness/benchmarks``.
2. Decorate the class with ``@register_agent("name")`` /
   ``@register_benchmark("name")``.

``load_builtins`` imports the adapter packages so their decorators fire. The CLI
and tests then resolve names via ``get_agent`` / ``get_benchmark``.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Callable, TypeVar

from harness.agent import Agent
from harness.benchmark import Benchmark

_AGENTS: dict[str, type[Agent]] = {}
_BENCHMARKS: dict[str, type[Benchmark]] = {}

A = TypeVar("A", bound=Agent)
B = TypeVar("B", bound=Benchmark)


def register_agent(name: str) -> Callable[[type[A]], type[A]]:
    def deco(cls: type[A]) -> type[A]:
        if name in _AGENTS:
            raise ValueError(f"agent {name!r} already registered")
        cls.name = name
        _AGENTS[name] = cls
        return cls

    return deco


def register_benchmark(name: str) -> Callable[[type[B]], type[B]]:
    def deco(cls: type[B]) -> type[B]:
        if name in _BENCHMARKS:
            raise ValueError(f"benchmark {name!r} already registered")
        cls.name = name
        _BENCHMARKS[name] = cls
        return cls

    return deco


def _import_all(package_name: str) -> None:
    pkg = importlib.import_module(package_name)
    for mod in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{package_name}.{mod.name}")


def load_builtins() -> None:
    """Import the built-in adapter packages to populate the registry."""
    _import_all("harness.agents")
    _import_all("harness.benchmarks")


def _filter_kwargs(cls: type, kwargs: dict) -> dict:
    """Keep only kwargs the class ``__init__`` accepts.

    Lets the CLI pass a superset of options (e.g. tau-bench's ``--user-model``)
    without breaking adapters that don't accept them. If ``__init__`` declares
    ``**kwargs`` it accepts everything.
    """
    sig = inspect.signature(cls.__init__)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(kwargs)
    accepted = set(sig.parameters) - {"self"}
    return {k: v for k, v in kwargs.items() if k in accepted}


def get_agent(name: str, **kwargs) -> Agent:
    if name not in _AGENTS:
        raise KeyError(f"unknown agent {name!r}; available: {sorted(_AGENTS)}")
    cls = _AGENTS[name]
    return cls(**_filter_kwargs(cls, kwargs))


def get_benchmark(name: str, **kwargs) -> Benchmark:
    if name not in _BENCHMARKS:
        raise KeyError(
            f"unknown benchmark {name!r}; available: {sorted(_BENCHMARKS)}"
        )
    cls = _BENCHMARKS[name]
    return cls(**_filter_kwargs(cls, kwargs))


def agent_class(name: str) -> type[Agent]:
    """The registered agent *class* (for introspection without instantiating)."""
    if name not in _AGENTS:
        raise KeyError(f"unknown agent {name!r}; available: {sorted(_AGENTS)}")
    return _AGENTS[name]


def benchmark_class(name: str) -> type[Benchmark]:
    """The registered benchmark *class* (for introspection without instantiating)."""
    if name not in _BENCHMARKS:
        raise KeyError(f"unknown benchmark {name!r}; available: {sorted(_BENCHMARKS)}")
    return _BENCHMARKS[name]


def available_agents() -> list[str]:
    return sorted(_AGENTS)


def available_benchmarks() -> list[str]:
    return sorted(_BENCHMARKS)
