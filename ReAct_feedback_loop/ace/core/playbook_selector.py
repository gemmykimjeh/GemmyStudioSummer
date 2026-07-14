"""
Playbook Selector for ACE system.

A fast-timescale Thompson-sampling bandit (AEL idea) that chooses which of the
two playbooks the Generator reads on each task: the "abstract" store or the
"concrete" store. Each arm keeps a Beta(a, b) posterior over its success rate;
we sample from both posteriors and read the winner. The benchmark signal
(success/failure of the shown playbook) drives the posterior update.

Both playbooks keep learning every task regardless of which one is *read* — the
selector governs reading only.
"""

import json
import random
from typing import Dict, Optional

ARMS = ("abstract", "concrete")
DEFAULT_SECTION = "__default__"


class PlaybookSelector:
    """Thompson-sampling bandit over the {abstract, concrete} reading arms.

    Args:
        context: "global" keeps a single shared posterior (keyed by
            ``__default__``); "section" keeps a separate posterior per section
            key passed to ``sample``/``update``.
        seed_posteriors: optional persisted posteriors dict to resume from
            (see ``to_dict``/``from_dict``).
    """

    def __init__(self, context: str = "global", seed_posteriors: Optional[Dict] = None):
        if context not in ("global", "section"):
            raise ValueError(f"context must be 'global' or 'section', got {context!r}")
        self.context = context
        # posteriors[section][arm] = {"a": float, "b": float}; Beta(1, 1) prior.
        self.posteriors: Dict[str, Dict[str, Dict[str, float]]] = {}
        if seed_posteriors:
            for section, arms in seed_posteriors.items():
                self.posteriors[section] = {
                    arm: {"a": float(v.get("a", 1.0)), "b": float(v.get("b", 1.0))}
                    for arm, v in arms.items()
                }

    def _key(self, section: Optional[str]) -> str:
        """Resolve the posterior key for the current context."""
        if self.context == "global" or not section:
            return DEFAULT_SECTION
        return section

    def _get(self, section: Optional[str]) -> Dict[str, Dict[str, float]]:
        """Fetch (creating on demand) the per-arm posteriors for a section."""
        key = self._key(section)
        if key not in self.posteriors:
            self.posteriors[key] = {arm: {"a": 1.0, "b": 1.0} for arm in ARMS}
        return self.posteriors[key]

    def sample(self, section: Optional[str] = None) -> str:
        """Thompson-sample an arm: draw Beta(a, b) per arm, return the winner."""
        arms = self._get(section)
        draws = {arm: random.betavariate(arms[arm]["a"], arms[arm]["b"]) for arm in ARMS}
        return max(draws, key=draws.get)

    def update(self, section: Optional[str], arm: str, signal: bool) -> None:
        """Beta update: success bumps ``a``, failure bumps ``b``."""
        if arm not in ARMS:
            raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
        arms = self._get(section)
        if signal:
            arms[arm]["a"] += 1.0
        else:
            arms[arm]["b"] += 1.0

    def mean(self, section: Optional[str], arm: str) -> float:
        """Posterior mean success rate a / (a + b) for an arm."""
        arms = self._get(section)
        a, b = arms[arm]["a"], arms[arm]["b"]
        return a / (a + b)

    def to_dict(self) -> Dict:
        """Serialize for JSON persistence next to the playbooks."""
        return {"context": self.context, "posteriors": self.posteriors}

    @classmethod
    def from_dict(cls, data: Dict) -> "PlaybookSelector":
        """Rebuild a selector from ``to_dict`` output."""
        return cls(context=data.get("context", "global"),
                   seed_posteriors=data.get("posteriors"))

    def save(self, path: str) -> None:
        """Persist posteriors to a JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "PlaybookSelector":
        """Load posteriors from a JSON file."""
        with open(path, "r") as f:
            return cls.from_dict(json.load(f))
