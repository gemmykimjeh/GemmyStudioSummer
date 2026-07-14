"""browsecomp — BrowseComp browsing QA (real).

(a) License:        MIT (openai/simple-evals). Templates + XOR decrypt below are
                    adapted from that repo's ``browsecomp_eval.py``.
(b) Infrastructure: LIGHT — no Docker/VM, so no ``--env-endpoint``. Needs network
                    (to fetch the public test CSV) and ``ANTHROPIC_API_KEY``:
                    browsing is done with Anthropic's server-side web tools and
                    grading with an Anthropic judge. Precheck errors clearly if
                    the key is missing.
(c) Scoring:        the official BrowseComp method — a grader model judges the
                    agent's answer against the (decrypted) reference and emits
                    ``correct: yes|no`` -> 1.0/0.0. No LLM-judge substitution;
                    this *is* the upstream grader.
(d) Official repo:  https://github.com/openai/simple-evals (browsecomp_eval.py)

The Env exposes `web_search` / `web_fetch` tools so the agent under test can
actually browse (BrowseComp questions are near-impossible without it). Those
tools are fulfilled inside the benchmark via Anthropic's server-side
`web_search`/`web_fetch`, keeping browsing the benchmark's concern — the agent
just calls generic tools. Configure the browsing/grader models via constructor
args (`browse_model`, `grader_model`), separate from the agent's own model.
"""

from __future__ import annotations

import base64
import csv
import functools
import hashlib
import io
import os
import re
import urllib.request

from harness.benchmark import Benchmark, Env
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory

_DATASET_URL = (
    "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv"
)

# Verbatim from simple-evals (HLE-derived); do not paraphrase — grading depends on it.
QUERY_TEMPLATE = """
{Question}

Your response should be in the following format:
Explanation: {{your explanation for your final answer}}
Exact Answer: {{your succinct, final answer}}
Confidence: {{your confidence score between 0% and 100% for your answer}}
""".strip()

GRADER_TEMPLATE = """
Judge whether the following [response] to [question] is correct or not based on the precise and unambiguous [correct_answer] below.

[question]: {question}

[response]: {response}

Your judgement must be in the format and criteria specified below:

extracted_final_answer: The final exact answer extracted from the [response]. Put the extracted answer as 'None' if there is no exact, final answer to extract from the response.

[correct_answer]: {correct_answer}

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_answer], focusing only on if there are meaningful differences between [correct_answer] and the extracted_final_answer. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_answer], focus only on whether the answers match.

correct: Answer 'yes' if extracted_final_answer matches the [correct_answer] given above, or is within a small margin of error for numerical problems. Answer 'no' otherwise, i.e. if there if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.


confidence: The extracted confidence score between 0|\\%| and 100|\\%| from [response]. Put 100 if there is no confidence score available.
""".strip()

_INSTRUCTIONS = (
    "You are a meticulous research assistant with `web_search` and `web_fetch` "
    "tools. The question requires looking up specific facts on the web — you MUST "
    "use the tools to research; do not answer from memory. Search, fetch the most "
    "promising sources, cross-check, then answer strictly in the requested "
    "Explanation / Exact Answer / Confidence format."
)


def _derive_key(password: str, length: int) -> bytes:
    hasher = hashlib.sha256()
    hasher.update(password.encode())
    key = hasher.digest()
    return key * (length // len(key)) + key[: length % len(key)]


def decrypt(ciphertext_b64: str, password: str) -> str:
    """XOR-decrypt a base64 ciphertext with a SHA256-derived key (simple-evals)."""
    encrypted = base64.b64decode(ciphertext_b64)
    key = _derive_key(password, len(encrypted))
    return bytes(a ^ b for a, b in zip(encrypted, key)).decode()


@functools.lru_cache(maxsize=1)
def _load_rows() -> list[dict]:
    try:
        with urllib.request.urlopen(_DATASET_URL, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"BrowseComp: could not fetch the test set from {_DATASET_URL} "
            f"({exc}). Check network access."
        ) from exc
    return list(csv.DictReader(io.StringIO(text)))


def _anthropic():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "BrowseComp needs ANTHROPIC_API_KEY (for browsing via Anthropic "
            "server-side web tools and for the grader). Set it and re-run."
        )
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "BrowseComp needs the anthropic SDK: pip install -e \".[claude]\"."
        ) from exc
    return anthropic.Anthropic()


def _text_of(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text")


def _parse_verdict(grading: str) -> str:
    """Extract the official ``correct: yes|no`` verdict; default 'no'."""
    m = re.search(r"correct: (yes|no)", grading)
    return m.group(1) if m else "no"


class BrowseCompEnv(Env):
    """Exposes web_search/web_fetch (via Anthropic server tools) for one question."""

    def __init__(self, task: Task, browse_model: str, max_web_tokens: int) -> None:
        self.task = task
        self.browse_model = browse_model
        self.max_web_tokens = max_web_tokens
        self._client = None
        self.browse_calls = 0
        self.browse_tokens = 0

    def _cli(self):
        if self._client is None:
            self._client = _anthropic()
        return self._client

    def observation(self) -> str:
        return self.task.prompt

    def instructions(self) -> str:
        return _INSTRUCTIONS

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(name="web_search",
                     description="Search the web and return relevant findings "
                                 "(titles, URLs, snippets) for a query.",
                     parameters={"type": "object",
                                 "properties": {"query": {"type": "string"}},
                                 "required": ["query"]}),
            ToolSpec(name="web_fetch",
                     description="Fetch a specific URL and return its key content.",
                     parameters={"type": "object",
                                 "properties": {"url": {"type": "string"}},
                                 "required": ["url"]}),
        ]

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        args = arguments or {}
        try:
            if name == "web_search":
                q = args.get("query", "")
                return ToolResult(output=self._browse(
                    "web_search_20260209", "web_search",
                    f"Search the web for: {q}\n\nReturn the most relevant results "
                    "with their URLs and a one-line snippet each."))
            if name == "web_fetch":
                url = args.get("url", "")
                return ToolResult(output=self._browse(
                    "web_fetch_20260209", "web_fetch",
                    f"Fetch this URL and report its key content relevant to the "
                    f"user's research: {url}"))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"Error: {type(exc).__name__}: {exc}",
                              is_error=True)
        return ToolResult(output=f"unknown tool {name!r}", is_error=True)

    def _browse(self, tool_type: str, tool_name: str, prompt: str) -> str:
        resp = self._cli().messages.create(
            model=self.browse_model,
            max_tokens=self.max_web_tokens,
            tools=[{"type": tool_type, "name": tool_name}],
            messages=[{"role": "user", "content": prompt}],
        )
        self.browse_calls += 1
        u = resp.usage
        self.browse_tokens += (u.input_tokens or 0) + (u.output_tokens or 0)
        text = _text_of(resp)
        return text or "[no textual results returned]"


@register_benchmark("browsecomp")
class BrowseComp(Benchmark):
    """Official BrowseComp: browse to answer, graded 0/1 vs the reference answer."""

    def __init__(
        self,
        browse_model: str = "claude-sonnet-4-6",
        grader_model: str = "claude-sonnet-4-6",
        max_web_tokens: int = 3000,
    ) -> None:
        self.browse_model = browse_model
        self.grader_model = grader_model
        self.max_web_tokens = max_web_tokens

    def load_tasks(self, limit: int | None = None) -> list[Task]:
        rows = _load_rows()
        if limit is not None:
            import random
            rows = random.Random(0).sample(rows, min(limit, len(rows)))
        tasks = []
        for i, row in enumerate(rows):
            problem = decrypt(row["problem"], row["canary"])
            answer = decrypt(row["answer"], row["canary"])
            tasks.append(Task(
                id=f"browsecomp-{i:04d}", benchmark="browsecomp",
                prompt=QUERY_TEMPLATE.format(Question=problem),
                metadata={"problem": problem, "answer": answer},
            ))
        return tasks

    def setup(self, task: Task) -> Env:
        _anthropic()  # precheck: key present (raises with clear guidance otherwise)
        return BrowseCompEnv(task, self.browse_model, self.max_web_tokens)

    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        response = trajectory.final_output or ""
        grader_prompt = GRADER_TEMPLATE.format(
            question=task.metadata["problem"],
            correct_answer=task.metadata["answer"],
            response=response,
        )
        client = _anthropic()
        g = client.messages.create(
            model=self.grader_model, max_tokens=1024,
            messages=[{"role": "user", "content": grader_prompt}],
        )
        verdict = _parse_verdict(_text_of(g))
        success = verdict == "yes"
        browse_calls = getattr(env, "browse_calls", 0)
        return Result(
            task_id=task.id, benchmark=self.name, agent="",
            success=success, score=1.0 if success else 0.0,
            metrics={
                "graded": verdict,
                "answered": bool(response.strip()),
                "browse_calls": browse_calls,
                "browse_tokens": getattr(env, "browse_tokens", 0),
                "num_tool_calls": len(trajectory.tool_calls()),
            },
            cost=trajectory.cost, wall_time=trajectory.wall_time,
        )
