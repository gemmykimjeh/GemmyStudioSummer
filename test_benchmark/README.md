# agent-bench-harness

Plug any agent (harness) into any of **7 benchmark suites** behind two clean
abstractions. New agents and new benchmarks are added by writing one adapter and
registering it — **no changes to existing code**.

```
python -m harness.run --agent claude_sdk --benchmark tau_bench --limit 5
```

---

## Architecture

The whole design is two abstract interfaces that never import each other. They
meet only through a generic **`Env`** — the tool boundary that keeps agents and
benchmarks mutually ignorant.

```
                        Task  ─────────────┐
                          ▲                 │ prompt + metadata
             load_tasks() │                 ▼
        ┌──────────────────────┐     ┌──────────────┐     ┌──────────────────┐
        │      Benchmark       │     │     Env      │     │      Agent       │
        │  (per-suite adapter) │     │  (generic    │     │ (per-harness     │
        │                      │     │   tool iface)│     │   adapter)       │
        │  setup(task) ────────┼────▶│  tools()     │◀────┤  run(task, env)  │
        │                      │     │  call_tool() │     │                  │
        │  score(task,         │     │  observation │     │  produces        │
        │        trajectory,   │◀────┤  snapshot()  │────▶│  Trajectory      │
        │        env) ─▶ Result│     │  close()     │     │                  │
        └──────────────────────┘     └──────────────┘     └──────────────────┘
                 │                    ▲          ▲                  │
                 │   the ONLY types that cross the boundary:        │
                 └── Task · Trajectory · Result · ToolSpec · ToolResult ──┘
```

* **`Agent`** (`harness/agent.py`) — implements `run(task, env) -> Trajectory`.
  The agent discovers tools via `env.tools()` and acts via `env.call_tool(...)`;
  it never learns which benchmark it is inside.
* **`Benchmark`** (`harness/benchmark.py`) — implements `load_tasks`, `setup`
  (build the `Env`), `score` (grade into a `Result`), and optional `teardown`.
  **Each benchmark grades in its own way** (final-state check, pass/fail,
  LLM-judge, answer match) but always returns the shared `Result`. There is
  deliberately **no universal grader**.
* **`Env`** (`harness/benchmark.py`) — the decoupling boundary. Whether a tool
  drives a VM, a Docker container, an HTTP mock, or an in-memory simulator is
  entirely the benchmark's concern; the agent only sees `ToolSpec`s.
* **Shared schema** (`harness/schema.py`) — `Task`, `Trajectory`, `Result`,
  `ToolSpec`, `ToolResult`. Wildly different scoring schemes (0/1, partial
  credit, Elo, judge score) all normalize into one `Result`.

> **Note on the `run` signature.** The original spec sketched `run(self, task)`.
> A real agent must *act* on the environment, so `run` also receives the generic
> `Env`. Passing the tool boundary explicitly is exactly what keeps agents and
> benchmarks decoupled.

### Orchestrator

`harness/runner.py` drives `load_tasks → setup → agent.run → score → teardown`
with parallel execution, per-task timeout, failure isolation (one dead task
never aborts the sweep), live JSONL checkpointing with resume, and a CSV
summary. `harness/run.py` is the CLI.

---

## 아키텍처 (한국어)

### 한 줄 요약
**아무 에이전트나 아무 벤치마크에나 꽂을 수 있게** 만든 평가 하네스다. 비결은
딱 하나 — 에이전트와 벤치마크가 **서로를 절대 import 하지 않고**, 오직 **`Env`라는
공용 도구 경계**를 통해서만 만난다.

### 핵심 아이디어: 두 개의 추상 인터페이스 + 그 사이의 Env
- **`Agent`** (`harness/agent.py`): `run(task, env) -> Trajectory` 하나만 구현.
  에이전트는 `env.tools()`로 쓸 수 있는 도구를 발견하고 `env.call_tool(...)`로
  행동한다. **자기가 어떤 벤치 안에 있는지 전혀 모른다.**
- **`Benchmark`** (`harness/benchmark.py`): `load_tasks`(태스크 목록) / `setup`(태스크용
  `Env` 생성) / `score`(→ `Result`로 채점) / `teardown`. **채점 방식은 벤치마다
  자유**(pass/fail, 부분점수, LLM 심판, 정답 매칭…)지만 결과는 항상 공용 `Result`로.
  **공용(universal) 채점기는 일부러 안 만들었다.**
- **`Env`** (`harness/benchmark.py`): **디커플링 경계.** 그 도구가 VM을 굴리든,
  Docker 컨테이너든, HTTP mock이든, 인메모리 시뮬이든 — 그건 **벤치마크의 사정**이고
  에이전트는 `ToolSpec` 목록만 본다. 메서드: `tools()`(도구 스펙) · `call_tool(name,
  args)`(도구 실행) · `observation()`(초기 관측) · `instructions()`(도메인 정책) ·
  `snapshot()`(채점용 상태) · `close()`.

### 경계를 넘는 것은 딱 5개 타입뿐 (`harness/schema.py`)
`Task`(할 일) · `Trajectory`(에이전트가 한 행동 기록: `Step` 리스트 + 최종 출력 +
토큰/비용/시간) · `Result`(성공여부·score·metrics로 정규화된 채점 결과) ·
`ToolSpec`(도구 정의) · `ToolResult`(도구 실행 결과). 이 5개 외엔 아무것도 안 넘어간다.
그래서 0/1·부분점수·Elo·심판점수 같은 전혀 다른 채점이 모두 하나의 `Result`로 비교된다.

### 데이터 흐름 (한 태스크)
```
Benchmark.load_tasks() → Task
      → Benchmark.setup(task) → Env                (벤치가 컨테이너/VM/시뮬 등을 준비)
      → Agent.run(task, env) → Trajectory          (에이전트가 env.tools()/call_tool()로 행동)
      → Benchmark.score(task, trajectory, env) → Result   (벤치가 공식 방식으로 채점)
      → Benchmark.teardown(env)                    (자원 정리)
```
`harness/runner.py`가 이 루프를 돌린다: **병렬 실행**, 태스크별 **타임아웃**, **실패
격리**(한 태스크가 죽어도 전체 스윕은 안 멈춤), **JSONL 체크포인트/재개**, pass^k 집계.

### 왜 "갈아끼우기"가 되나 — 레지스트리 + 코어 무수정
- `harness/registry.py`: `@register_agent("이름")` / `@register_benchmark("이름")`
  데코레이터로 등록. CLI는 이름으로 조회. `_filter_kwargs`가 각 어댑터의 `__init__`이
  받는 인자만 골라 넘겨줘서, CLI가 벤치별 옵션을 뭉텅이로 넘겨도 안전하다.
- **새 에이전트/새 벤치 추가 = 어댑터 파일 1개 + 데코레이터 1줄.** 코어(runner/registry/
  schema/Env)는 **한 줄도 안 고친다.** (Hermes·Claude Agent SDK 에이전트를 추가할 때도
  코어 수정 0이었다 — 이게 아키텍처가 지켜졌다는 증거.)

### 에이전트 어댑터들 — 각자 "어떻게 Env에 adapt 하나" (브리징은 어댑터 안에만)
- **`scripted`**: `task.metadata["oracle_actions"]`를 재생. API 키 없이 파이프라인
  전체를 도는 오프라인/CI 드라이버.
- **`claude_sdk`**: **원시 Anthropic Messages API + 우리가 손으로 짠 도구 루프**.
  `env.tools()`를 Anthropic 도구 정의로 변환 → 모델이 도구 호출 → `env.call_tool`로
  실행 → 결과 되먹임 → 반복. 모델 1개.
- **`claude_agent_sdk`**: **Anthropic의 진짜 Claude Agent SDK 프레임워크**(자체 에이전트
  루프). 우리 `Env` 도구를 **인프로세스 MCP 도구**(`@tool`/`create_sdk_mcp_server`,
  `mcp__env__*`)로 브리지하고, `can_use_tool` 게이트로 **우리 도구만 허용·호스트 내장도구
  거부**. SDK가 루프를 돌리고 우리는 결과만 `Trajectory`로 수집.
- **`hermes`**: 별도 프로젝트(NousResearch Hermes)를 **서브프로세스**로 구동(자기 venv/
  계정), `Env`를 **MCP 서버**로 노출해 브리지. 우리 venv에 import 안 함.

→ 4개 모두 `Agent.run(task, env)->Trajectory`만 만족하면 되고, **에이전트별 특유 코드는
전부 그 어댑터 파일 안에만** 있다(공용 헬퍼: `agents/_react.py` 트랜스크립트 변환,
`agents/_mcp_bridge.py` Env→MCP). 코어는 어떤 에이전트도 특별 취급하지 않는다.

### 벤치마크 어댑터들 — upstream을 Env로 감싸고 "공식 방식" 그대로 채점
7개 전부 real. 각 어댑터는 (a) upstream에서 태스크 로드 (b) `Env`로 실행환경 제공
(c) **그 벤치의 공식 채점**을 그대로 호출한다. 무거운 인프라(Docker/VM)는 로컬 자동
프로비저닝 대신 사전점검 + `--env-endpoint`/provider 선택으로 처리(자세한 건 아래 각 섹션).

### 실행 = 재현 가능한 run 디렉토리
`python -m harness.run ...` 한 번 = `runs/<run-id>/` 하나. 안에 `manifest.jsonl`(설정
스냅샷 + git/Hermes 커밋·모델·버전 등 provenance) + `<벤치>__<에이전트>/`(results.jsonl,
summary.csv, passk_summary.csv). `python -m harness.report runs/<run-id>/`가 이걸 읽어
**연결/검증 상태표 + 점수표 + 라이선스 각주**가 든 마크다운 리포트를 만든다(`harness/
report.py`, 읽기 전용). `harness/repro.py`가 provenance를 수집한다.

### 새로 추가하려면 (체크리스트)
- **에이전트**: `harness/agents/<이름>.py`에 `Agent` 상속 + `run(task,env)` 구현 +
  `@register_agent("이름")`. 끝. (아래 [Adding a new agent](#adding-a-new-agent))
- **벤치마크**: `harness/benchmarks/<이름>.py`에 `Benchmark`(+필요시 `Env`) 구현 +
  `@register_benchmark("이름")`. 끝. (아래 [Adding a new benchmark](#adding-a-new-benchmark))

---

## The 7 benchmarks

| Name | Phase | Infra | Scoring | Upstream |
|------|-------|-------|---------|----------|
| `tau_bench` | **1 — REAL** (mock fallback) | upstream sim + LLM user | upstream reward → pass^k | sierra-research/tau-bench |
| `automation_bench` | **1 — REAL** (mock fallback) | upstream sim (no user LLM) | deterministic assertions → pass rate | zapier/AutomationBench |
| `osworld` | **REAL** | desktop VM (attach via `--env-endpoint`) | official `DesktopEnv.evaluate()` | xlang-ai/OSWorld |
| `swe_bench` | **REAL** | Docker (swebench in a runner image) | official harness (FAIL_TO_PASS / PASS_TO_PASS) | swe-bench/SWE-bench |
| `terminal_bench` | **REAL** | Docker | official pytest tests (resolved iff all pass) | laude-institute/terminal-bench |
| `browsecomp` | **REAL** | web browsing | official grader vs reference (0/1) | openai/simple-evals |
| `gdpval` | **REAL** | none (LLM judge) | official rubric (rubric_json) | OpenAI GDPval gold |

> **`tau_bench` and `automation_bench` are REAL by default** — they drive the
> actual upstream suites (see [tau-bench](#tau-bench-real-vs-mock) and
> [automation-bench](#automation-bench-real-vs-mock) below). Pass `--no-real`
> for the offline mock fallback of either.
>
> **⚠️ `--no-real` mock mode is `[MOCK]`** — a **SHAPE-FAITHFUL MOCK, not
> upstream.** It reproduces the upstream *shape* (tools + stateful env + the
> same grading style) with a compact in-memory simulator so the pipeline runs
> offline with no keys, but it does **not** run the real suite and its numbers
> are **not** official scores. Every mock run is tagged `[MOCK]` in the CLI
> output and the results CSV/JSONL (`mock` column/field) to prevent confusion.

Phase-2 adapters implement the full `Benchmark` interface; `load_tasks`/`setup`/
`score` raise `NotImplementedError` with a per-repo run recipe (see each file's
docstring). Filling one in is purely additive.

WildClawBench is intentionally excluded (OpenClaw-runtime dependent) — see
[`docs/DEFERRED.md`](docs/DEFERRED.md).

### Agents

| Name | Status | Notes |
|------|--------|-------|
| `scripted` | real | Replays `task.metadata["oracle_actions"]` — the offline/CI driver; runs the full pipeline with no API key. |
| `claude_sdk` | real | **Raw Anthropic Messages API + our own function-calling loop** (single model, `claude-opus-4-8` default). Needs the `claude` extra + `ANTHROPIC_API_KEY`. |
| `claude_agent_sdk` | real | **Anthropic's real Claude Agent SDK framework** (its own agentic loop/context management; powers Claude Code). Our Env tools are bridged in as in-process MCP tools and a `can_use_tool` gate blocks host built-ins. Needs the `claude_agent_sdk` extra + `ANTHROPIC_API_KEY`. See [claude_agent_sdk](#claude_agent_sdk-real-agent-framework). |
| `hermes` | real | NousResearch Hermes, driven as a subprocess (its own venv + account). **B-mode** bridges the benchmark's `Env` over MCP; **A-mode** (`--native-tools`) runs Hermes's own toolset in `env.workspace()`. See [hermes](#hermes-real-via-mcp-bridge). |

These are interchangeable: `--agent scripted|claude_sdk|claude_agent_sdk|hermes` against **any** benchmark. `claude_sdk` vs `claude_agent_sdk` is the key contrast — the *same* Claude model behind *our* minimal loop vs Anthropic's full agent framework — so you can A/B "model + our scaffolding" against "model + Anthropic's scaffolding" on identical tasks.

---

## Install

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"        # core + tests (no model SDK needed)
uv pip install -e ".[dev,claude]" # add the Anthropic SDK for claude_sdk
```

Heavy per-benchmark dependencies are isolated as extras (`.[osworld]`,
`.[swe_bench]`, ...) so the core stays light.

**For real `tau_bench`** — clone the upstream repo and install its extra:

```bash
git clone https://github.com/sierra-research/tau-bench.git external/tau-bench
uv pip install -e ".[tau_bench]"   # pulls litellm (the user-simulator backend)
```

(Or point `TAU_BENCH_PATH` at an existing clone.) Real runs also need API keys:
one for the agent model (`ANTHROPIC_API_KEY` for `claude_sdk`) and one for the
user-simulator provider (`OPENAI_API_KEY` for the default `gpt-4o` user).

**For real `automation_bench`** — clone the upstream repo and install its extra:

```bash
git clone https://github.com/zapier/AutomationBench.git external/AutomationBench
uv pip install -e ".[automation_bench]"   # pulls datasets
```

(Or point `AUTOMATION_BENCH_PATH` at an existing clone.) Scoring is
deterministic (no user-simulator LLM), so a real run only needs the **agent**
key. On Windows the CLI auto-enables Python UTF-8 mode (the upstream reads UTF-8
data files); for direct API use, set `PYTHONUTF8=1`.

---

## Team onboarding

**1. Get the upstream benchmark repos** into `external/` (idempotent; `-Update`
to pull):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/clone_external.ps1
```

This clones OSWorld, SWE-bench, terminal-bench, tau-bench, AutomationBench, and
simple-evals (BrowseComp). GDPval needs no clone — it loads the `openai/gdpval`
Hugging Face dataset.

**2. Keys** — copy `.env.example` to `.env` and fill in what you need. Nothing is
required for the offline `[MOCK]` demos.

| key | used by |
| --- | --- |
| `ANTHROPIC_API_KEY` | `claude_sdk` agent, BrowseComp (browse+grade), GDPval judge |
| `OPENAI_API_KEY` | tau-bench default user-simulator (`gpt-4o`) |
| `HF_TOKEN` (optional) | faster HF dataset pulls (SWE-bench, GDPval) |

**3. What can I run with my infra?**

| I have… | Benchmarks I can run |
| --- | --- |
| **nothing** (offline) | `tau_bench --no-real`, `automation_bench --no-real` with `--agent scripted` — deterministic `[MOCK]` connection demos |
| **API keys only** | `tau_bench` (real), `automation_bench` (real), `browsecomp`, `gdpval` |
| **+ Docker Desktop (WSL2)** | `swe_bench`, `terminal_bench` |
| **+ a desktop VM provider** | `osworld` (OSWorld-Verified) via `--provider docker` (slow on WSL2, no KVM), `vmware` (best local), or `aws` (parallel) |

**4. Adding an agent or benchmark** — see [Adding a new agent](#adding-a-new-agent)
and [Adding a new benchmark](#adding-a-new-benchmark) below (one adapter + one
registry decorator; no core edits).

---

## Run

```bash
# offline demo — no API key, deterministic scores via the scripted oracle
python -m harness.run --agent scripted --benchmark tau_bench

# the headline demo — a real Claude agent scoring τ-bench (needs ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=sk-ant-...
python -m harness.run --agent claude_sdk --benchmark tau_bench --limit 5

python -m harness.run --list            # registered agents + benchmarks
```

Useful flags: `--limit N`, `--k N` (trials/task for pass^k), `--concurrency N`,
`--timeout SECONDS`, `--output-dir DIR`, `--resume`, `--model claude-opus-4-8`,
`--max-steps N`. tau-bench: `--real`/`--no-real`, `--split test`,
`--user-model gpt-4o`, `--user-provider openai`, `--user-strategy llm`.

Each execution writes one **run directory** `runs/<run-id>/` (run-id defaults to a
UTC timestamp; pass `--run-id` to group several benchmarks into one). Inside:
`manifest.jsonl` (config snapshot + provenance: git & Hermes commit, models,
versions) and, per benchmark×agent, `<benchmark>__<agent>/` with `results.jsonl`
(one `Result` per line, appended live), `summary.csv`, and `passk_summary.csv`.
Turn a run directory into a markdown report (status matrix + scores + licenses):

```bash
python -m harness.report runs/<run-id>/            # one run
python -m harness.report runs/<old>/ runs/<new>/   # A/B or before/after diff
```

## tau-bench (real vs mock)

`tau_bench` runs the **real** upstream suite by default:

```bash
# real upstream: retail + airline test tasks, pass^3, per-domain CSV
python -m harness.run --agent claude_sdk --benchmark tau_bench --limit 20 --k 3
```

* **Domains** — `load_tasks()` returns both `retail` (115) and `airline` (50)
  test tasks, round-robin interleaved so a small `--limit` still spans both.
  (τ2/telecom lives in the separate `tau2-bench` repo — not wired here.)
* **User simulator** — an LLM playing the user, configured *separately* from the
  agent model via `--user-model` / `--user-provider` / `--user-strategy`. So you
  can drive a Claude agent against a GPT-4o user.
* **Scoring** — the upstream reward (final-DB-state hash match to the gold
  action sequence + required-output check), 0/1 per task. The harness aggregates
  the official **pass^k** = mean over tasks of `C(c, k) / C(n, k)` (c = passing
  trials of n), alongside **pass@1**. Broken down by domain in
  `passk_summary.csv`. Real runs are **not** `[MOCK]`-tagged.
* **Cost/tokens/wall-time** — `Result.cost` sums agent cost + user-sim cost;
  `metrics` carries `agent_cost`, `user_cost`, `agent_tokens`.
* **Fallback** — `--no-real` uses a compact offline mock (tagged `[MOCK]`) that
  needs no repo/keys, for smoke-testing the pipeline.

## automation-bench (real vs mock)

`automation_bench` runs the **real** upstream zapier/AutomationBench public task
set by default:

```bash
python -m harness.run --agent claude_sdk --benchmark automation_bench --limit 20 --max-steps 50
```

* **Domains** — `load_tasks()` returns the 6 business domains (Sales, Marketing,
  Operations, Support, Finance, HR × 100 each), round-robin interleaved. The
  200-task `simple` domain is a **baseline, excluded from the official score**:
  add it with `include_simple=True` and it's tagged `baseline_only` so the
  domain pass-rate and report omit it.
* **World & tools** — each task seeds a fully-simulated SaaS world (pydantic
  `WorldState` is the source of truth: CRM, inbox, calendar, sheets, …). The
  agent gets a REST-style toolset (`api_search` / `api_fetch` / `base64_encode`)
  and a **50-step budget** (`--max-steps 50`; the env also hard-caps tool calls).
  No user-simulator LLM — the agent acts, then stops.
* **Scoring — deterministic, no LLM judge** — `partial_credit` (fraction of
  assertions satisfied) → `Result.score`; `task_completed_correctly` (1.0 only
  if *every* assertion passes) → `Result.success`. The average of the strict
  metric over scored tasks is the pass rate.
* **False-success detection** — `metrics['false_success_claim']` is `True` when
  the agent's final message claims completion but the deterministic score is a
  fail. `report()` prints the rate per domain + overall (a real sonnet run over
  6 tasks showed 0/6 strict passes but an **83% false-success-claim rate** —
  the model repeatedly thought it had finished when it hadn't).
* **Per-domain breakdown + caveat** — `report()` prints pass rate and
  false-success rate per domain and notes that this is the **PUBLIC** task set;
  the official leaderboard is scored on a **private held-out set**, so local
  numbers are directional and won't match it 1:1.
* **Fallback** — `--no-real` uses a compact offline mock (tagged `[MOCK]`).

---

## gdpval (real, light — LLM judge only)

`gdpval` runs **GDPval** (`openai/gdpval` — 220 open "gold" tasks across 44
occupations / 9 sectors), *not* the Artificial-Analysis "GDPval-AA" variant. Each
task is an open-ended professional deliverable; the Env is **tool-free** — the
agent's final message *is* the deliverable. Scoring uses the **official
`rubric_json`** that ships with every task (a checklist of pointed criteria): an
LLM grader decides which criteria the deliverable meets, and the score is
`earned / max positive points`, clamped to `[0, 1]` — the same rubric artifact
OpenAI's automated grader uses. Human expert pairwise grading is GDPval's gold
standard but isn't automatable here.

```powershell
python -m harness.run --benchmark gdpval --limit 5   # needs ANTHROPIC_API_KEY (the judge)
```

* **No VM/Docker.** The only requirement is `ANTHROPIC_API_KEY` for the rubric
  judge; `setup` prechecks it with clear guidance. Set the judge model via the
  `grader_model` constructor arg (separate from the agent's own model).
* **Verified end-to-end (real API, 1 task):** a Private-Investigator task scored a
  real **0.227** (5/22 rubric points) — the agent returned text where the task
  wanted PDF files, so file-format criteria went unmet. That is a faithful
  reflection of a text-only agent on a file-deliverable benchmark; the scoring is
  the official rubric, unaltered. (Many GDPval deliverables are files — a
  file-producing agent would score higher on those criteria.)

## osworld — OSWorld-Verified (real, needs a desktop VM)

`osworld` runs **OSWorld-Verified** (the corrected task set + evaluators in the
current xlang-ai/OSWorld repo; `test_all.json` = 369 tasks). Each task drives a
real Ubuntu desktop; the agent acts with a `pyautogui` tool (a Python snippet run
in the VM) and observes the accessibility tree; scoring is the **official
`DesktopEnv.evaluate()`** on the verified evaluators (reward `[0, 1]`;
`success == reward >= 1.0`). No substitution.

The harness drives a **real OSWorld provider** so the VM lifecycle is faithful —
per-task snapshot-revert (`vmware`/`virtualbox`) or a fresh clean container/
instance (`docker`/`aws`). That per-task reset is what makes a run comparable to
the verified leaderboard (our earlier "attach to a running VM" mode skipped it and
was **not** verified-faithful; it survives only as `--provider attach` for wiring
smoke-tests).

```powershell
python -m harness.run --benchmark osworld --provider docker --limit 3
python -m harness.run --benchmark osworld --provider vmware --meta test_nogdrive.json
python -m harness.run --benchmark osworld --provider aws --region us-east-1
```

* **You provision the provider; the harness drives it.** Each provider has a
  precise precheck (below). `--meta test_nogdrive.json` runs 361 tasks and needs
  **no Google account**; `test_all.json` adds 8 Google-Drive tasks that need OAuth.

| `--provider` | You must have | Notes |
| --- | --- | --- |
| `docker` (default) | Docker Desktop running; ~30 GB free | Auto-pulls `happysixd/osworld-docker` + downloads the guest qcow2 to `./docker_vm_data/`. **No `/dev/kvm` on Windows/WSL2 → software-emulated, slow** (fine for small `--limit`). |
| `vmware` | VMware Workstation Pro + `vmrun` on PATH | Best local fidelity; image auto-downloads, `init_state` snapshot auto-created + reverted per task. |
| `aws` | AWS creds + `AWS_REGION`/`AWS_SUBNET_ID`/`AWS_SECURITY_GROUP_ID`, client security group | Host-client, parallel ("~1 hour"). AMI pre-provided per region; `snapshot_name` auto-resolves to it. |
| `attach` | a guest already running (`--env-endpoint HOST:5000`) | **Non-verified** wiring smoke-test — no snapshot-revert. |

* **Google-Drive tasks (8/369):** need a blank Google account + a Google Cloud
  project (Drive API) + OAuth2 desktop credentials in
  `external/OSWorld/evaluation_examples/settings/google/` (see that repo's
  `SETUP_GUIDELINE.md` §1). Skip them with `--meta test_nogdrive.json`.
* **Verified here (no VM, no API):** the 369-task set loads; `test_nogdrive` is
  exactly 8 fewer; and every provider precheck fires with actionable guidance
  (missing `vmrun` / AWS env / Docker daemon / OSWorld deps). A real scored run
  needs a provisioned provider VM + OSWorld's heavy deps, so it is user-triggered.

## swe-bench (real, Docker)

`swe_bench` runs SWE-Bench Verified (500 instances from
`princeton-nlp/SWE-bench_Verified`). `setup` clones the repo at `base_commit`
into `/testbed` in a lightweight container; the agent fixes the issue via a
`bash` tool; `score` takes the agent's `git diff` as the `model_patch` and runs
the **official `swebench.harness.run_evaluation`** — an instance is *resolved*
iff all FAIL_TO_PASS + PASS_TO_PASS tests pass. No substitution.

```powershell
python -m harness.run --benchmark swe_bench --limit 5 --timeout 5400
```

* **Docker-only, no host Python deps for scoring.** The official swebench harness
  is Unix-only (it `import resource`s at import time), so it *cannot* run on
  native Windows Python. Instead `score` runs it inside a small Linux **runner
  image** (`harness-swebench-runner`, built automatically on first use) that
  drives the host Docker daemon via the mounted socket. This works identically on
  Windows / macOS / Linux — the only host dependency is `datasets` (to load the
  task set) plus Docker itself.
* The official eval then builds/pulls per-instance **multi-GB** images and runs
  the real test suite on the host daemon. Use local Docker or `--env-endpoint`
  (`DOCKER_HOST`). If Docker is missing/broken, `setup` errors clearly and
  `score` surfaces the runner-image build failure instead of a bogus number.
* **Verified runnable here (no API, no eval):** data loads (500 instances); the
  agent environment works end-to-end (container + `git clone @ base_commit` +
  `bash` + `git diff` patch extraction, checked live on `psf/requests`); the
  runner image builds; swebench imports inside it; `run_evaluation --help` works;
  and the runner reaches the host daemon through the socket. The only step *not*
  run is a full scored instance (multi-GB image builds + the agent solving a real
  bug over the API) — that is user-triggered.

## terminal-bench — Terminal-Bench 2.0 (real, Docker)

`terminal_bench` runs the real **Terminal-Bench 2.0** dataset (the harbor-era 89
tasks), *not* the older terminal-bench-core / original tasks. Each 2.0 task ships
a **prebuilt image** (`[environment].docker_image` in its `task.toml`), so setup
just pulls + runs it; the agent solves the task through a single `bash` tool
(`docker exec`); then scoring reproduces harbor's verifier contract — copy the
task's `tests/` in and run its `tests/test.sh`, which runs pytest and writes
`/logs/verifier/reward.txt` (1/0). **We report that official reward.** No
substitution, no reimplemented grader.

```powershell
# tasks live at external/terminal-bench-2 (clone via scripts/clone_external.ps1)
python -m harness.run --benchmark terminal_bench --tasks chess-best-move
python -m harness.run --benchmark terminal_bench --limit 3 --timeout 1200
```

* **Infra**: needs Docker (Docker Desktop / WSL2 on Windows). Use the local
  daemon, or point `--env-endpoint tcp://HOST:2375` at a prepared/remote Docker
  (`DOCKER_HOST`). If neither is reachable, `setup` fails with a precise precheck.
  First run of a task **pulls its multi-hundred-MB image** (`alexgshaw/<task>:…`).
* **Tasks**: `git clone https://github.com/laude-institute/terminal-bench-2
  external/terminal-bench-2` (or set `TERMINAL_BENCH_TASKS`). `--tasks a,b,c` runs
  a subset; `--limit N` takes the first N.
* **Why not harbor directly**: harbor is 2.0's official *runner*, but it drives
  its own agents (oracle/claude-code). To plug **our** agent behind the generic
  `Env`, we reproduce harbor's container + verifier contract instead — the
  scoring artifact (`test.sh` → `reward.txt`) is the official one, unchanged.
* **Scope note**: the agent phase runs with networking on (most 2.0 tasks set
  `allow_internet = true`, and the verifier fetches uv+pytest); `allow_internet
  = false` isolation is recorded but not enforced in v1.

## claude_agent_sdk (real agent framework)

`claude_agent_sdk` plugs Anthropic's **Claude Agent SDK** (`claude-agent-sdk` —
the framework that powers Claude Code) into the harness as a peer of `claude_sdk`,
`scripted`, and `hermes`. The core/runner/registry are untouched; all the bridging
lives in `harness/agents/claude_agent_sdk.py`.

* **What's different from `claude_sdk`**: `claude_sdk` calls the raw Anthropic
  Messages API and runs *our* hand-written tool loop. `claude_agent_sdk` hands the
  whole agentic loop (planning, tool orchestration, context management) to the
  **SDK itself** — we only bridge tools and collect results.
* **Env bridge**: each benchmark `Env` tool becomes an **in-process MCP tool**
  (`@tool` + `create_sdk_mcp_server`, exposed as `mcp__env__<name>`). A
  `can_use_tool` gate **allows only `mcp__env__*`** and denies every built-in
  (Bash/Read/Write/WebSearch/...), so the SDK acts on the benchmark's sandbox, not
  the host filesystem — the `Env` stays the sole boundary.
* **Telemetry**: token usage + the SDK's own `total_cost_usd` are read from its
  `ResultMessage` into the harness `Trajectory`.
* **Runtime**: `pip install -e ".[claude_agent_sdk]"` (the Claude Code engine is
  bundled in the wheel — no separate CLI/Node install) + `ANTHROPIC_API_KEY`.

```powershell
python -m harness.run --agent claude_agent_sdk --benchmark gdpval --limit 1
# A/B the two scaffoldings on identical tasks:
python -m harness.run --agent claude_sdk       --benchmark terminal_bench --tasks fix-git
python -m harness.run --agent claude_agent_sdk --benchmark terminal_bench --tasks fix-git
```

### Two selectable modes: B (default) and A (`--native-tools`)

The same agent runs either way — you pick per run:

| Mode | Flag | Tools the agent uses | Measures | Works on |
| --- | --- | --- | --- | --- |
| **B** (default) | *(none)* | ONLY the env's bridged `mcp__env__*` tools (gate blocks host built-ins) | reasoning on a **fixed toolset** (apples-to-apples across agents) | **every** benchmark |
| **A** | `--native-tools` | the SDK's **own** built-in tools (Bash/Read/Write/Edit/…) on `env.workspace()` | the agent system **with its own tooling** (real-leaderboard style) | **A-capable only** |

```powershell
# B-mode (fixed env toolset) — any benchmark
python -m harness.run --agent claude_agent_sdk --benchmark swe_bench --limit 1
# A-mode (native tools) — A-capable benchmark only
python -m harness.run --agent claude_agent_sdk --native-tools --benchmark swe_bench --limit 1
```

* **A-capability is a benchmark property** (`Env.workspace()`): a benchmark whose
  task is editing files exposes a local checkout (**A-capable**: `swe_bench`).
  Benchmarks whose environment is a container/VM/tool-API are **B-only**
  (`terminal_bench`, `osworld`, `tau_bench`, `automation_bench`, `gdpval`,
  `browsecomp`) — running `--native-tools` on them raises a clear error telling
  you to drop the flag. This "some benchmarks are B-only" fact is enforced, not
  just documented.
* **Limitation**: the SDK `query` is single-prompt, so the conversational
  user-simulator protocol (`Env.respond`; real τ-bench) isn't driven by this agent
  yet — it targets tool-based and tool-free envs.

## hermes (real, via MCP bridge)

The `hermes` agent drives the separate NousResearch Hermes project (at
`C:/hermes/hermes-agent`) **without importing it** — Hermes exact-pins older
`anthropic`/`pydantic`, so it's run as a **subprocess with its own `.venv`**, and
nothing under `C:/hermes` is modified.

Like `claude_agent_sdk`, `hermes` supports **both A and B modes** (pick per run):

| Mode | Flag | Tools | Works on |
| --- | --- | --- | --- |
| **B** (default) | *(none)* | the benchmark's `Env` tools, bridged to Hermes over MCP | **every** benchmark |
| **A** | `--native-tools` | Hermes's **own** toolset (`development`: files/terminal/search/…) in `env.workspace()` | **A-capable only** (`swe_bench`) — B-only benches error clearly |

**B-mode (tool-bridge).** Hermes only calls tools in its own registry, so we
expose the benchmark's `Env` over an in-process **MCP server** (`_mcp_bridge.py`,
loopback Streamable HTTP). A driver (`_hermes_driver.py`, launched with Hermes's
venv) registers those MCP tools as a custom Hermes toolset and runs one
conversation; Hermes's tool calls route back over MCP to the same `Env` in our
process, which the benchmark then scores.

**A-mode (native tools).** No MCP bridge: the driver `chdir`s into the
benchmark's local `env.workspace()` and runs Hermes with its own `development`
toolset (constructor `native_toolsets`), so Hermes edits that directory with its
own file/terminal tools. Requires an A-capable benchmark; B-only benchmarks raise
the same "B-mode ONLY" error as `claude_agent_sdk`.

Either way, Hermes's ReAct steps become `Trajectory` steps and scoring is
unchanged (state/answer based, agent-agnostic).

Verified end-to-end: `--agent hermes` drives the Hermes subprocess, which calls
the benchmark's env tools over MCP (e.g. `get_order_details` → `cancel_order` →
`message_user`) and the result is scored — swappable with `claude_sdk` on the
same task set.

**Parameters** (constructor / A/B knobs): `hermes_path`, `hermes_home` (the
memory/skills/notepads workspace — swap it to A/B memory setups), `model`
(default `claude-opus-4-8`; override with `--model`), `provider` (default
`anthropic`), `max_steps`, `use_own_account` (default `True` → uses Hermes's own
Claude account and strips our keys; `False` lets it borrow `ANTHROPIC_API_KEY`).
Paths resolve from args → `HERMES_PATH`/`HERMES_HOME` env → defaults; never
hardcoded.

> **Two Hermes-side prerequisites** (independent of the harness):
> 1. **Account login.** Hermes authenticates via `C:/hermes/.home/.claude`. If
>    that login is missing/expired its model calls fail. Log in once with
>    `C:/hermes/login-hermes-account.cmd` → `/login`.
> 2. **Native provider + explicit model.** Hermes's main path otherwise resolves
>    to an OpenAI-style client on Anthropic's URL (→ HTTP 404 on
>    `/chat/completions`) with a blank model. The adapter defaults
>    `provider="anthropic"` (native `/v1/messages`) and `model="claude-opus-4-8"`
>    to avoid this; pass `--model claude-haiku-4-5` for cheaper runs.
>
> If Hermes still can't reach its model you'll see the bridge work (env tools
> registered) but **0 tool calls** — that's a Hermes config/auth issue, not the
> integration.

### Comparing agents

Run two agents on the same task set, then build a comparison table:

```powershell
python -m harness.run --agent hermes     --benchmark tau_bench --limit 10
python -m harness.run --agent claude_sdk --benchmark tau_bench --limit 10
python -m harness.compare --benchmark tau_bench --agents hermes claude_sdk
```

`compare` aligns per-task results and prints pass rate + mean score per agent,
writing `runs/compare_<benchmark>_<agents>.csv`.

## A/B experiments

`harness/experiment.py` measures the **performance delta from a config change** —
fix (benchmark set, task subset, base model), vary one thing across named
**conditions**, run each N times, and report per-condition pass rate with a
bootstrap CI, the delta vs a baseline (percentage points, paired-bootstrap CI so
you can tell signal from noise), and the cost change. Outputs `summary.csv`,
`deltas.csv`, and a grouped bar chart (conditions × benchmarks). Install the
extra: `uv pip install -e ".[experiment]"` (pyyaml + matplotlib).

```powershell
python -m harness.experiment --config configs/loop_ab.yaml            # run it
python -m harness.experiment --config configs/loop_ab.yaml --dry-run  # just print the plan
python -m harness.experiment --config configs/demo_ab.yaml            # offline demo (no key/cost)
```

A **condition** is just an agent name + `agent_args`, so it can flip any knob
with zero runner changes — e.g. Hermes `hermes_home` for a **memory/skills
workspace A/B** (`configs/loop_ab.yaml`: `memory_off` = empty workspace vs
`memory_on` = the populated one), `claude_sdk` vs `hermes`, or a
`loop_off`/`loop_on` ablation. Config schema:

```yaml
name: my_ab
base_model: claude-haiku-4-5          # injected as each agent's model (ignored if unaccepted)
benchmarks: [tau_bench, automation_bench]
benchmark_args: { tau_bench: {real: false}, automation_bench: {real: false} }
repeats: 3                            # trials per (condition, task) -> variance for CIs
limit: 3                              # task subset (same tasks across conditions)
seed: 42                              # bootstrap RNG (reproducible CIs)
baseline: memory_off                  # deltas are computed vs this condition
conditions:
  - { name: memory_off, agent: hermes, agent_args: { hermes_home: ".../empty" } }
  - { name: memory_on,  agent: hermes, agent_args: { hermes_home: "C:/hermes/.hermes" } }
output_dir: runs/exp_my_ab
```

`configs/demo_ab.yaml` is a fully offline verification (scripted `loop_off` vs
`loop_on`) — it produces a real, significant delta with no API key. The Hermes
memory A/B (`loop_ab.yaml`) needs Hermes working; the memory effect is strongest
on **real** benchmarks (`real: true`, needs keys) — on the mock tasks the delta
is expected to be small/noise.

## Adding a new agent

**Checklist** (one new file + one decorator — no core/benchmark edits):
1. Create `harness/agents/<name>.py`.
2. Subclass `Agent`; implement `run(self, task, env) -> Trajectory`.
3. In `run`, discover tools via `env.tools()`, act via `env.call_tool(...)`
   (and `env.respond(...)` if `env.conversational`), record `Step`s.
4. Set `traj.final_state = env.snapshot()` before returning.
5. Decorate the class with `@register_agent("<name>")`.
6. `--agent <name>` now works on every benchmark. (External/subprocess agents:
   reuse `EnvMCPBridge` + `messages_to_trajectory` — see below.)

```python
# harness/agents/my_agent.py
from harness.agent import Agent
from harness.registry import register_agent
from harness.schema import Trajectory

@register_agent("my_agent")
class MyAgent(Agent):
    def run(self, task, env) -> Trajectory:
        traj = Trajectory()
        for spec in env.tools():
            ...  # decide what to call
        result = env.call_tool("some_tool", {"arg": 1})
        traj.final_state = env.snapshot()
        return traj
```

That's it — `--agent my_agent` now works on **every** benchmark. See
`harness/agents/claude_sdk.py` for the full tool-loop + cost-tracking pattern.

**Adding an *external / subprocess* agent** (like Hermes) never bottlenecks on
plumbing — three agent-agnostic pieces are reused, so a new adapter only writes
its own process orchestration:

- `harness/agents/_mcp_bridge.py` — `EnvMCPBridge(env)` exposes **any** `Env`'s
  tools over MCP (loopback HTTP). External agents that speak MCP call the env
  through it; the in-process env mutates and is scored as usual.
- `harness/agents/_react.py` — `messages_to_trajectory(messages)` converts any
  OpenAI-/ShareGPT-style transcript into a `Trajectory` (thought/action/observation).
- The `Agent` interface + `Env` boundary are unchanged.

So a new subprocess agent = start `EnvMCPBridge`, run the agent's own CLI/driver
against `bridge.url`, then `messages_to_trajectory(...)`. No benchmark or core
change.

## Adding a new benchmark

**Checklist** (one new file + one decorator — no core/agent edits):
1. Create `harness/benchmarks/<name>.py` with an `Env` subclass implementing
   `tools()`, `call_tool()`, and (for state-graded benches) `snapshot()`.
2. Subclass `Benchmark`; implement `load_tasks(limit)`, `setup(task) -> Env`,
   `score(task, trajectory, env) -> Result`. Grade however you like — always
   return the common `Result` (no shared grader).
3. Optional hooks: `Env.instructions()` (domain policy for the agent),
   `Benchmark.mock = True` (auto `[MOCK]` tagging), `Benchmark.report(results)`
   (custom summary printed by the CLI).
4. Decorate the class with `@register_benchmark("<name>")`.
5. `--benchmark <name>` now works with every agent (the runner stamps the agent
   name + `mock` flag onto each `Result`).

```python
# harness/benchmarks/my_bench.py
from harness.benchmark import Benchmark, Env
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec

class MyEnv(Env):
    def __init__(self, task): self.task = task
    def tools(self): return [ToolSpec(name="do", description="...")]
    def call_tool(self, name, arguments): return ToolResult(output="ok")
    def snapshot(self): return {...}

@register_benchmark("my_bench")
class MyBench(Benchmark):
    def load_tasks(self, limit=None): return [Task(id="1", benchmark="my_bench", prompt="...")]
    def setup(self, task): return MyEnv(task)
    def score(self, task, trajectory, env) -> Result:
        return Result(task_id=task.id, benchmark=self.name, agent="",
                      success=True, score=1.0)
```

`--benchmark my_bench` now works with **every** agent. (The runner stamps the
agent name onto the `Result` for you.)

---

## Tests

```bash
pytest            # runs offline; no API key or network required
```

At least one unit test per interface (`Agent`, `Benchmark`/`Env`, schema,
registry) plus end-to-end runs of both Phase-1 benchmarks and the orchestrator
(checkpoint/resume, failure isolation, per-task timeout).

---

## Limitations

* **Timeouts** use `concurrent.futures` cancellation, which cannot forcibly kill
  a worker thread already inside a blocking call. A timed-out task is *reported*
  as such and its slot freed, but a wedged thread may linger until its call
  returns. For hard isolation, move to a process pool or an external watchdog.
* The τ-bench user is *scripted*, not LLM-driven, and both Phase-1 domains are
  compact in-memory reproductions — faithful in *shape* (tools + state grading)
  to the upstream suites, sized to run end-to-end offline. Each adapter's
  docstring explains how to swap in the real upstream environment.
