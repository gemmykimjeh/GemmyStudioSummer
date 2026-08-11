"""Playbook — the evolving context store for ACE (arXiv 2510.04618).

Paper vocabulary throughout: a *playbook* holds *bullets* (strategies). The
Curator applies incremental *delta operations* rather than rewriting the
playbook wholesale — the paper identifies full rewrites as the cause of
"context collapse", where accumulated detail is silently lost.

Stdlib only, so this is unit-testable without a model, a network, or GAIA.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable


# Bullets whose normalised texts exceed this ratio are treated as the same
# strategy. 0.82 merges paraphrases ("check for a second attachment" vs "check
# whether a second file is attached") while keeping distinct strategies apart.
DUPLICATE_THRESHOLD = 0.82


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — comparison only."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


@dataclass
class Bullet:
    """A single reusable strategy."""

    text: str
    tags: list[str] = field(default_factory=list)
    helpful: int = 0
    harmful: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_by_task: str | None = None
    updated_at: float = field(default_factory=time.time)

    @property
    def score(self) -> int:
        return self.helpful - self.harmful


class Playbook:
    """An ordered collection of bullets with delta-update semantics."""

    def __init__(self, bullets: list[Bullet] | None = None) -> None:
        self.bullets: list[Bullet] = list(bullets or [])

    # ---------------------------------------------------------------- lookup

    def __len__(self) -> int:
        return len(self.bullets)

    def get(self, bullet_id: str) -> Bullet | None:
        return next((b for b in self.bullets if b.id == bullet_id), None)

    def find_duplicate(self, text: str) -> Bullet | None:
        """Return an existing bullet expressing the same strategy, if any."""
        candidate = normalise(text)
        if not candidate:
            return None
        for b in self.bullets:
            if SequenceMatcher(None, candidate, normalise(b.text)).ratio() >= DUPLICATE_THRESHOLD:
                return b
        return None

    # ------------------------------------------------------- delta operations

    def add(self, text: str, tags: list[str] | None = None,
            task_id: str | None = None) -> tuple[Bullet, bool]:
        """ADD. Returns (bullet, created). Merges into a duplicate if one exists.

        Merging rather than rejecting preserves signal: a strategy rediscovered
        on a second task is evidence that it generalises.
        """
        existing = self.find_duplicate(text)
        if existing is not None:
            for t in tags or []:
                if t not in existing.tags:
                    existing.tags.append(t)
            existing.updated_at = time.time()
            return existing, False
        bullet = Bullet(text=text.strip(), tags=list(tags or []), created_by_task=task_id)
        self.bullets.append(bullet)
        return bullet, True

    def update(self, bullet_id: str, text: str) -> bool:
        """UPDATE — refine an existing bullet's wording in place."""
        b = self.get(bullet_id)
        if b is None:
            return False
        b.text = text.strip()
        b.updated_at = time.time()
        return True

    def remove(self, bullet_id: str) -> bool:
        """DELETE."""
        b = self.get(bullet_id)
        if b is None:
            return False
        self.bullets.remove(b)
        return True

    def mark(self, helpful: Iterable[str] = (), harmful: Iterable[str] = ()) -> None:
        """Record outcome attribution against bullets that were in context."""
        for bid in helpful:
            b = self.get(bid)
            if b:
                b.helpful += 1
                b.updated_at = time.time()
        for bid in harmful:
            b = self.get(bid)
            if b:
                b.harmful += 1
                b.updated_at = time.time()

    def prune(self, min_score: int = -2) -> list[Bullet]:
        """Drop bullets that have proved consistently harmful. Returns removed."""
        doomed = [b for b in self.bullets if b.score <= min_score]
        for b in doomed:
            self.bullets.remove(b)
        return doomed

    # -------------------------------------------------------------- rendering

    def _ranked(self, limit: int | None = None) -> list[Bullet]:
        ranked = sorted(self.bullets, key=lambda b: (-b.score, b.updated_at))
        return ranked[:limit] if limit is not None else ranked

    def as_prompt(self, limit: int | None = None) -> str:
        """Render for injection into AgentContext.system_message_suffix.

        Ordered by score so the most-validated strategies lead. IDs are shown so
        the Reflector can refer to specific bullets.
        """
        ranked = self._ranked(limit)
        if not ranked:
            return ""
        head = (
            "## Learned strategies\n\n"
            "Strategies distilled from earlier tasks in this run. Apply the ones\n"
            "that fit; ignore the ones that do not. They are heuristics, not rules.\n"
        )
        return head + "\n" + "\n".join(f"- [{b.id}] {b.text}" for b in ranked)

    def visible_ids(self, limit: int | None = None) -> list[str]:
        """IDs that as_prompt(limit) would show — used for outcome attribution."""
        return [b.id for b in self._ranked(limit)]

    # ------------------------------------------------------------ persistence

    def to_dict(self) -> dict:
        return {"version": 1, "bullets": [asdict(b) for b in self.bullets]}

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "Playbook":
        p = Path(path)
        if not p.exists():
            return cls()
        payload = json.loads(p.read_text())
        return cls([Bullet(**b) for b in payload.get("bullets", [])])