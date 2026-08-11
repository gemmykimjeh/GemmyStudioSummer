# Vendored ACE

Source: `GemmyStudioSummer/ReAct` (the unrevised ACE implementation, **not**
`ReAct_feedback_loop` and **not** `ace-appworld`).

Everything here is a verbatim copy except for the edits listed below. The intent
is that the Reflector, the Curator, their prompts, and all playbook string
manipulation behave exactly as they do in the reference, so that a result can be
attributed to ACE rather than to a local reinterpretation of it.

## Files copied verbatim

| Vendored path | Reference path |
|---|---|
| `ace/core/reflector.py` | `ace/core/reflector.py` |
| `ace/core/curator.py` | `ace/core/curator.py` |
| `ace/core/generator.py` | `ace/core/generator.py` |
| `ace/core/bulletpoint_analyzer.py` | `ace/core/bulletpoint_analyzer.py` |
| `ace/prompts/{reflector,curator,generator}.py` | same |
| `playbook_utils.py` | `playbook_utils.py` |
| `logger.py` | `logger.py` |

## Edits

Import rewrites only — no logic changed. The reference repo is flat, so its
modules import each other absolutely; here they are inside a package.

| File | Before | After |
|---|---|---|
| `ace/core/{reflector,generator,curator}.py` | `from llm import timed_llm_call` | `from ...llm import timed_llm_call` |
| `ace/core/curator.py` | `from playbook_utils import ...` | `from ...playbook_utils import ...` |
| `ace/core/curator.py` | `from logger import ...` | `from ...logger import ...` |
| `logger.py` | `from playbook_utils import parse_playbook_line` | `from .playbook_utils import parse_playbook_line` |
| `playbook_utils.py` | `from utils import get_section_slug` | `from .utils import get_section_slug` |

## Files written for this integration, not copied

- **`llm.py`** — replaces the reference `llm.timed_llm_call`, which is bound to a
  SambaNova client and an API-key mixer. Same signature and same return shape,
  routed through litellm against the harness's own LLM config. It preserves the
  `INCORRECT_DUE_TO_EMPTY_RESPONSE` sentinel because `Curator.curate` branches on
  that prefix.
- **`utils.py`** — carries only `get_section_slug`, copied verbatim, so bullet ID
  slugs match the reference. The reference `utils.py` pulls in benchmark-specific
  data processors that are irrelevant here.

## Not vendored

`ace/ace.py` — the `ACE` orchestrator. Its outer loops (epochs, evaluation
windows, `best_playbook` tracking, the parallel test path) are replaced by the
OpenHands `Evaluation` loop. Only `_train_single_sample`'s per-sample logic is
needed, and it is ported in `ace_gaia/runner.py` with the call sequence and
ordering preserved.
