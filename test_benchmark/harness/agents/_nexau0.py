"""_nexau0 — the NexAU0 seed substrate shared by the Terminal-Bench 2.0 ACE arms.

Why this exists
---------------
The AHE paper (Agentic Harness Engineering, arXiv:2604.25850) does **not** run ACE
on ACE's native ReAct scaffold; it runs ACE on **NexAU0**, the deliberately
minimal NexAU *seed* harness, so the surrounding harness is held FIXED and only
the context layer (ACE's playbook) varies. That is the apples-to-apples setup:
"same seed, does the gain come from evolving context or from evolving the
harness?". Our TB2 arms used to sit on a richer, hand-written shell prompt +
loop, which is a *different* substrate than the one AHE compared ACE on.

This module re-grounds both arms (``ace_terminal``, ``ace_dual_terminal``) on a
faithful, self-contained re-implementation of the NexAU0 seed, so they are now
**ACE-on-NexAU0** — the exact substrate AHE used for its ACE comparison.

What NexAU0 is (per the paper), reproduced here
-----------------------------------------------
An intentionally minimal seed:
  * a SINGLE tool, ``run_shell_command`` (shell is the only action space);
  * a minimal system prompt = one tool + three behavioural rules + three
    runtime-injected variables (date, user, working directory);
  * NO middleware, NO skills, NO sub-agents, NO long-term memory.

We do NOT vendor the NexAU framework — the harness-fixed comparison only needs
the *seed*, and re-implementing it keeps the arms runnable offline with no new
dependency.

Deliberate choices
------------------
* The seed's single ``run_shell_command`` is mapped onto whatever shell tool the
  ``Env`` exposes (``bash`` for terminal_bench) at call time, so the model only
  ever sees ``run_shell_command`` while any shell-shaped Env still works.
* The seed prompt and the ``run_shell_command`` schema/description are VERBATIM
  from the repo (``systemprompt.md`` and ``run_shell_command.tool.yaml``), so the
  model sees the exact seed interface ACE saw — including the ``command``,
  ``description``, ``is_background`` and ``dir_path`` parameters.
* ``is_background`` cannot be reproduced faithfully: the real NexAU tool backgrounds
  at the process-group level, while the TB2 Env exposes only a foreground ``bash``.
  We emulate it in the adapter (nohup + ``&``); the model still never writes ``&``,
  so the seed's rule holds on the model side. This is the one irreducible gap.
* The benchmark's own ``env.instructions()`` is intentionally NOT folded into the
  prompt: the seed establishes the shell context itself, and adding
  benchmark-specific guidance would make the substrate richer than NexAU0.
* The ACE layer's contribution (its in-context playbook, or the learned-playbook
  + immutable-rulebook view) is passed as ``context_block`` and appended verbatim
  after the seed — that in-context read is the ONLY thing layered on the seed,
  which is exactly ACE's documented mechanism.
* No live probe for username/working_directory: that would spend a scored command
  every task (and skew ``num_commands`` between runs). ``date`` is real;
  ``username``/``working_directory`` are the container defaults (root / ``/``). The
  seed's three-variable *structure* is what matters for the harness-fixed
  comparison, not live values.
"""

from __future__ import annotations

import shlex
import time
from datetime import datetime
from typing import Callable

from harness.schema import Step

# The NexAU0 seed system prompt — VERBATIM from the AHE repo
# (agents/code_agent_simple/systemprompt.md). Only the Jinja ``{{ var }}`` markers
# are rewritten as Python ``.format`` fields; the prose is unchanged.
_SEED_TEMPLATE = """You solve software tasks in a non-interactive setting. Your only tool is **`run_shell_command`**: use the shell to inspect the repo, edit files, run builds/tests, and finish the work. Do not ask the user questions.

- Prefer short replies; use the tool for actions.
- Before commands that delete or overwrite important data, state briefly what they do.
- Long-running processes: use `is_background: true` on `run_shell_command` (do not use `&` in the command string).

Date: {date}
Username: {username}
Working Dir: {working_directory}"""

# The single seed tool, in Anthropic tool-format. Schema and description are
# VERBATIM from the seed's tool_descriptions/run_shell_command.tool.yaml: the four
# parameters (command, description, is_background, dir_path), command required.
SEED_TOOL = {
    "name": "run_shell_command",
    "description": (
        "This tool executes a given shell command as `bash -c <command>`. To run a "
        "command in the background, set the `is_background` parameter to true. Do NOT "
        "use `&` to background commands. Command is executed as a subprocess that "
        "leads its own process group. Command process group can be terminated as "
        "`kill -- -PGID` or signaled as `kill -s SIGNAL -- -PGID`.\n\n"
        "The following information is returned:\n\n"
        "Output: Combined stdout/stderr. Can be `(empty)` or partial on error and for "
        "any unwaited background processes.\n\n"
        "Exit Code: Only included if non-zero (command failed).\n\n"
        "Error: Only included if a process-level error occurred (e.g., spawn failure).\n\n"
        "Signal: Only included if process was terminated by a signal.\n\n"
        "Background PIDs: Only included if background processes were started.\n\n"
        "Process Group PGID: Only included if available."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Exact bash command to execute as `bash -c <command>`",
            },
            "description": {
                "type": "string",
                "description": ("Brief description of the command for the user. Be "
                                "specific and concise. Ideally a single sentence. Can "
                                "be up to 3 sentences for clarity. No line breaks."),
            },
            "is_background": {
                "type": "boolean",
                "description": ("(OPTIONAL) Set to true if this command should be run "
                                "in the background (e.g. for long-running servers or "
                                "watchers). The command will be started, allowed to run "
                                "for a brief moment to check for immediate errors, and "
                                "then moved to the background. If not provided, defaults "
                                "to false."),
            },
            "dir_path": {
                "type": "string",
                "description": ("(OPTIONAL) The path of the directory to run the command "
                                "in. If not provided, the project root directory is used. "
                                "Must be a directory within the workspace and must "
                                "already exist."),
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


def _shell_command(arguments: dict) -> str:
    """Map a ``run_shell_command`` call onto a single shell command string for the
    Env's foreground ``bash`` tool, honouring ``dir_path`` and ``is_background``.

    ``dir_path`` becomes ``cd <dir> && ...``. ``is_background`` is emulated with the
    shell (nohup + ``&``) because the TB2 Env exposes only a single foreground bash
    tool — the real NexAU tool backgrounds at the process-group level, which this
    Env cannot express. The MODEL still never writes ``&`` (it sets is_background),
    so the seed's rule holds on the model side; only the adapter emulates it.
    """
    args = arguments or {}
    cmd = args.get("command", "")
    if args.get("dir_path"):
        cmd = f"cd {shlex.quote(str(args['dir_path']))} && {cmd}"
    if args.get("is_background"):
        cmd = (f"nohup bash -lc {shlex.quote(cmd)} >/tmp/nexau0_bg.log 2>&1 & "
               f"echo \"[started in background, pid $!]\"")
    return cmd


def solve(
    *,
    client,
    model: str,
    max_tokens: int,
    max_steps: int,
    task,
    env,
    traj,
    price_for: Callable[[str], tuple[float, float]],
    context_block: str | None = None,
) -> tuple[str | None, str]:
    """Drive the NexAU0 seed loop; return ``(final_text, transcript)``.

    The seed exposes exactly one tool (``run_shell_command``) and a minimal
    prompt. ``context_block`` is the ACE layer's in-context contribution
    (playbook, or learned-playbook + immutable-rulebook view); it is appended
    after the seed prompt and is the only thing on top of the seed. Telemetry
    (tokens, cost, steps) is written into ``traj`` exactly as the previous
    hand-written loop did, so scoring/metrics are unaffected.
    """
    # The seed's single tool maps onto whatever shell tool the Env provides.
    _env_tools = env.tools()
    shell_tool = _env_tools[0].name if _env_tools else "bash"

    date = datetime.now().strftime("%Y-%m-%d")
    system = _SEED_TEMPLATE.format(date=date, username="root", working_directory="/")
    if context_block and context_block.strip():
        system += "\n\n" + context_block.strip()

    traj.add(Step(type="user_message", text=env.observation()))
    messages = [{"role": "user", "content": env.observation()}]
    in_price, out_price = price_for(model)
    trace_parts: list[str] = []
    last_text: str | None = None

    # Terminal-Bench bounds an attempt by TIME per task (task.toml
    # [agent].timeout_sec); max_steps is only a runaway-loop backstop.
    budget = float(task.metadata.get("agent_timeout_sec") or 0) or None
    t0 = time.perf_counter()

    for _ in range(max_steps):
        if budget and time.perf_counter() - t0 > budget:
            trace_parts.append(f"[agent budget of {budget:g}s exhausted]")
            break
        response = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            tools=[SEED_TOOL], messages=messages,
        )
        usage = response.usage
        traj.tokens += (usage.input_tokens or 0) + (usage.output_tokens or 0)
        traj.cost += ((usage.input_tokens or 0) * in_price
                      + (usage.output_tokens or 0) * out_price) / 1_000_000

        tool_uses = []
        turn_text: list[str] = []
        for block in response.content:
            if block.type == "text":
                turn_text.append(block.text)
                trace_parts.append(block.text)
                traj.add(Step(type="assistant_message", text=block.text))
            elif block.type == "tool_use":
                tool_uses.append(block)
        if turn_text:
            last_text = "\n".join(turn_text)

        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tu in tool_uses:
            if tu.name == "run_shell_command":
                # The seed tool: run its command (with is_background honoured) on
                # the Env's shell tool.
                result = env.call_tool(shell_tool, {"command": _shell_command(tu.input or {})})
            else:
                # Not part of the seed; pass through so any tool-shaped Env works.
                result = env.call_tool(tu.name, tu.input or {})
            traj.add(Step(type="tool_call", name=tu.name,
                          arguments=dict(tu.input or {}), output=result.output,
                          is_error=result.is_error))
            trace_parts.append(f"[{tu.name}({tu.input})] -> {result.output}")
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id,
                                 "content": result.output, "is_error": result.is_error})
        messages.append({"role": "user", "content": tool_results})

    return last_text, "\n".join(trace_parts)
