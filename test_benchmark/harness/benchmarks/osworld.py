"""osworld — OSWorld-Verified computer-use desktop tasks (real).

This targets **OSWorld-Verified**: the corrected task set + evaluators that live
in the current xlang-ai/OSWorld repo (the 2025-07-28 "OSWorld-Verified" update).
"Verified" is not a flag — it is the fixed `evaluation_examples/` + evaluators,
run through the standard `test_all.json` (369 tasks). See README.md:36 upstream.

(a) License:        Apache-2.0 (xlang-ai/OSWorld). Tasks: evaluation_examples/.
(b) Infrastructure: HEAVY — a real Ubuntu desktop VM per task, managed by one of
                    OSWorld's own providers so the VM lifecycle (snapshot-revert to
                    ``init_state`` for vmware/virtualbox, fresh clean container/
                    instance for docker/aws) is faithful — this is what makes a run
                    comparable to the verified leaderboard. YOU provision the
                    provider; the harness drives it:
                      - ``--provider docker``  (Docker Desktop; auto-downloads the
                        guest qcow2. No /dev/kvm on Windows/WSL2 => software-emulated
                        and slow, but works for small checks.)
                      - ``--provider vmware``  (VMware Workstation Pro + vmrun; best
                        local fidelity; image auto-downloaded, init_state snapshot).
                      - ``--provider aws``     (host-client EC2, parallel; needs
                        AWS creds + subnet + security group; AMI is pre-provided.)
                      - ``--provider attach`` + ``--env-endpoint HOST[:5000]`` — a
                        NON-verified smoke-test that attaches to an already-running
                        guest WITHOUT snapshot-revert (wiring check only).
                    Each provider has a precise precheck; missing prereqs / OSWorld
                    deps -> an actionable error (never a bogus score).
(c) Scoring:        the OFFICIAL ``DesktopEnv.evaluate()`` — runs the task's
                    verified ``evaluator`` against the live guest; reward in [0, 1].
                    success == (reward >= 1.0). No substitution.
(d) Official repo:  https://github.com/xlang-ai/OSWorld  (blog: xlang.ai/blog/osworld-verified)

Google-account note: 8 of the 369 tasks touch Google Drive and need OAuth
credentials (see SETUP_GUIDELINE §1). Use ``--meta test_nogdrive.json`` (361
tasks) to skip them without any Google setup.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from harness.benchmark import Benchmark, Env
from harness.benchmarks._util import external_repo_on_path, interleave
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory

_DEFAULT_HOME = Path(__file__).resolve().parents[2] / "external" / "OSWorld"
_DEFAULT_META = "test_all.json"  # the full OSWorld-Verified set (369 tasks)
_GUEST_PORT = 5000
_MAX_OUTPUT = 6000
_CLONE_HINT = (
    "OSWorld tasks not found at {repo}. Clone the repo:\n"
    "  git clone https://github.com/xlang-ai/OSWorld external/OSWorld\n"
    "  (or set OSWORLD_PATH to an existing checkout)."
)
_DEPS_HINT = (
    "OSWorld's Python deps are not installed (import desktop_env failed: {exc}).\n"
    "  cd external/OSWorld && pip install -e .   "
    "(heavy: torch, opencv, librosa, pymupdf, playwright, ...).")


def _probe(host: str, port: int, timeout: float = 5.0) -> bool:
    """True if an OSWorld guest server answers at http://host:port/screenshot."""
    try:
        with urllib.request.urlopen(
                f"http://{host}:{port}/screenshot", timeout=timeout) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 500
    except Exception:  # noqa: BLE001 - unreachable / refused / timeout
        return False


def _docker_ok() -> bool:
    try:
        return subprocess.run(["docker", "version"], capture_output=True,
                              timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _install_attach_provider(dm, host: str, port: int) -> None:
    """(attach mode only) monkeypatch the provider factory to a no-op that points
    DesktopEnv's controllers at an already-running guest. NOT verified-faithful —
    there is no snapshot-revert; use only to smoke-test harness wiring."""
    from desktop_env.providers.base import Provider, VMManager

    ip_ports = f"{host}:{port}:9222:8006:8080"

    class _AttachProvider(Provider):
        def start_emulator(self, *a, **k):
            pass

        def get_ip_address(self, path_to_vm):
            return ip_ports

        def save_state(self, *a, **k):
            pass

        def revert_to_snapshot(self, path_to_vm, snapshot_name=None):
            return path_to_vm

        def stop_emulator(self, *a, **k):  # never kill the user's VM
            pass

    class _AttachManager(VMManager):
        def initialize_registry(self, *a, **k):
            pass

        def add_vm(self, *a, **k):
            pass

        def delete_vm(self, *a, **k):
            pass

        def occupy_vm(self, *a, **k):
            pass

        def list_free_vms(self, *a, **k):
            return []

        def check_and_clean(self, *a, **k):
            pass

        def get_vm_path(self, *a, **k):
            return host

    dm.create_vm_manager_and_provider = lambda *a, **k: (_AttachManager(), _AttachProvider())


class OSWorldEnv(Env):
    """Wraps a live OSWorld ``DesktopEnv``; agent acts via `pyautogui`/`stop`.

    Observations are the guest's accessibility tree (text) — a legitimate,
    vision-free OSWorld setup matching the text-only Env boundary.
    """

    def __init__(self, task: Task, desktop, provider: str) -> None:
        self.task = task
        self.desktop = desktop  # a real desktop_env.DesktopEnv
        self.provider = provider
        self.n_actions = 0
        self._last_obs = None

    @staticmethod
    def _a11y(obs) -> str:
        if not obs:
            return "[no observation]"
        tree = obs.get("accessibility_tree")
        text = tree if isinstance(tree, str) and tree else "[accessibility tree unavailable]"
        if len(text) > _MAX_OUTPUT:
            text = text[:_MAX_OUTPUT] + "\n...[truncated]"
        return text

    def observation(self) -> str:
        return f"{self.task.prompt}\n\n[accessibility tree]\n{self._a11y(self._last_obs)}"

    def instructions(self) -> str:
        return (
            "You control a real Ubuntu desktop. Act with the `pyautogui` tool: "
            "`code` is a Python snippet executed in the VM with pyautogui already "
            "imported, e.g. `pyautogui.click(x, y)` or "
            "`pyautogui.typewrite('hello')`. After each action you receive the new "
            "accessibility tree. When the task is complete call `stop` with status "
            "'DONE' (or 'FAIL' if the task is infeasible)."
        )

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="pyautogui",
                description="Run a Python/pyautogui snippet in the desktop VM; "
                            "returns the resulting accessibility tree.",
                parameters={"type": "object",
                            "properties": {"code": {"type": "string"}},
                            "required": ["code"]},
            ),
            ToolSpec(
                name="stop",
                description="End the episode: 'DONE' if you completed the task, "
                            "'FAIL' if it is infeasible.",
                parameters={"type": "object",
                            "properties": {"status": {"type": "string",
                                                      "enum": ["DONE", "FAIL"]}},
                            "required": ["status"]},
            ),
        ]

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        args = arguments or {}
        try:
            if name == "pyautogui":
                obs, _r, _done, _info = self.desktop.step(args.get("code", ""))
                self.n_actions += 1
                self._last_obs = obs
                return ToolResult(output=self._a11y(obs))
            if name == "stop":
                status = str(args.get("status", "DONE")).upper()
                if status not in ("DONE", "FAIL"):
                    status = "DONE"
                obs, _r, _done, _info = self.desktop.step(status)
                self._last_obs = obs
                return ToolResult(output=f"[episode ended: {status}]")
            return ToolResult(output=f"unknown tool {name!r}", is_error=True)
        except Exception as exc:  # noqa: BLE001 - surface guest/transport errors
            return ToolResult(output=f"[action error] {exc}", is_error=True)

    def snapshot(self) -> dict:
        return {"provider": self.provider, "actions": self.n_actions}

    def close(self) -> None:
        try:
            self.desktop.close()
        except Exception:  # noqa: BLE001
            pass


@register_benchmark("osworld")
class OSWorld(Benchmark):
    """OSWorld-Verified; drives a real provider VM, official evaluate() scoring."""

    _AWS_ENV = ("AWS_REGION", "AWS_SUBNET_ID", "AWS_SECURITY_GROUP_ID",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")

    def __init__(
        self,
        provider: str = "docker",
        env_endpoint: str | None = None,          # only for provider="attach"
        path_to_vm: str | None = None,
        region: str | None = None,
        snapshot_name: str = "init_state",
        meta: str = _DEFAULT_META,
        action_space: str = "pyautogui",
        require_a11y_tree: bool = True,
        headless: bool = True,
        os_type: str = "Ubuntu",
        screen_size: tuple[int, int] = (1920, 1080),
        client_password: str = "",
        enable_proxy: bool = False,
    ) -> None:
        self.provider = provider
        self.env_endpoint = env_endpoint or os.environ.get("OSWORLD_ENDPOINT")
        self.path_to_vm = path_to_vm
        self.region = region or os.environ.get("AWS_REGION")
        self.snapshot_name = snapshot_name
        self.meta = meta
        self.action_space = action_space
        self.require_a11y_tree = require_a11y_tree
        self.headless = headless
        self.os_type = os_type
        self.screen_size = screen_size
        self.client_password = client_password
        self.enable_proxy = enable_proxy

    # -- tasks ------------------------------------------------------------
    def load_tasks(self, limit: int | None = None) -> list[Task]:
        repo = external_repo_on_path(
            _DEFAULT_HOME, "OSWORLD_PATH", "evaluation_examples", _CLONE_HINT)
        examples = repo / "evaluation_examples" / "examples"
        meta_path = repo / "evaluation_examples" / self.meta
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            pairs = interleave([[(dom, i) for i in ids]
                                for dom, ids in sorted(meta.items())])
        else:
            pairs = [(p.parent.name, p.stem)
                     for p in sorted(examples.glob("*/*.json"))]

        tasks: list[Task] = []
        for domain, ex_id in pairs:
            f = examples / domain / f"{ex_id}.json"
            if not f.is_file():
                continue
            cfg = json.loads(f.read_text(encoding="utf-8"))
            tasks.append(Task(
                id=ex_id, benchmark="osworld",
                prompt=cfg.get("instruction", ""),
                metadata={"domain": domain, "snapshot": cfg.get("snapshot"),
                          "task_config": cfg},
            ))
            if limit is not None and len(tasks) >= limit:
                break
        return tasks

    # -- setup ------------------------------------------------------------
    def _parse_endpoint(self) -> tuple[str, int]:
        ep = (self.env_endpoint or "").strip()
        if "://" in ep:
            ep = ep.split("://", 1)[1]
        ep = ep.rstrip("/")
        if ep.count(":") == 1:  # host:port (bracket IPv6)
            host, _, port = ep.partition(":")
            return host, int(port)
        return ep, _GUEST_PORT

    def _precheck_provider(self) -> None:
        """Fail fast with provider-specific guidance before any heavy import."""
        p = self.provider
        if p == "attach":
            if not self.env_endpoint:
                raise RuntimeError(
                    "provider='attach' needs a running guest: pass "
                    "--env-endpoint HOST[:5000] (or OSWORLD_ENDPOINT). NOTE: attach "
                    "is a wiring smoke-test only — no snapshot-revert, NOT a "
                    "verified run. For verified results use --provider "
                    "docker|vmware|aws.")
            host, port = self._parse_endpoint()
            if not _probe(host, port):
                raise RuntimeError(
                    f"cannot reach the OSWorld guest at http://{host}:{port}/screenshot. "
                    "Is the VM running with its in-VM server up?")
            return
        if p in ("vmware", "virtualbox"):
            exe = "vmrun" if p == "vmware" else "VBoxManage"
            if shutil.which(exe) is None:
                raise RuntimeError(
                    f"provider='{p}' needs {exe} on PATH. Install "
                    + ("VMware Workstation Pro (+ vmrun)" if p == "vmware"
                       else "VirtualBox (+ VBoxManage)")
                    + "; the OSWorld VM image auto-downloads on first run and the "
                      "'init_state' snapshot is created for you.")
            return
        if p == "aws":
            missing = [v for v in self._AWS_ENV if not os.environ.get(v)]
            if missing:
                raise RuntimeError(
                    "provider='aws' needs a host-client EC2 setup. Missing env: "
                    f"{', '.join(missing)}. Create a client security group (SSH/80/"
                    "5000/5910/8006/8080/8081/9222 inbound), note your VPC subnet, "
                    "add credentials, then set those vars. The OSWorld AMI is "
                    "pre-provided per region (see external/OSWorld AWS_GUIDELINE.md).")
            return
        if p == "docker":
            if not _docker_ok():
                raise RuntimeError(
                    "provider='docker' needs a running Docker daemon (Docker "
                    "Desktop / WSL2). First run auto-pulls happysixd/osworld-docker "
                    "and downloads the guest qcow2 to ./docker_vm_data/. On "
                    "Windows/WSL2 there is no /dev/kvm, so the VM is "
                    "software-emulated (slow) — fine for small checks.")
            return
        # any other provider name: let DesktopEnv validate it downstream

    def setup(self, task: Task) -> Env:
        self._precheck_provider()
        external_repo_on_path(
            _DEFAULT_HOME, "OSWORLD_PATH", "desktop_env",
            "OSWorld not found at {repo} (clone to external/OSWorld or set "
            "OSWORLD_PATH).")
        try:
            import desktop_env.desktop_env as dm
            from desktop_env.desktop_env import DesktopEnv
        except Exception as exc:  # noqa: BLE001 - heavy optional deps
            raise RuntimeError(_DEPS_HINT.format(exc=exc)) from exc

        kwargs = dict(
            provider_name=self.provider, action_space=self.action_space,
            require_a11y_tree=self.require_a11y_tree, headless=self.headless,
            os_type=self.os_type, screen_size=self.screen_size,
            snapshot_name=self.snapshot_name, enable_proxy=self.enable_proxy,
        )
        if self.region:
            kwargs["region"] = self.region
        if self.client_password:
            kwargs["client_password"] = self.client_password
        if self.path_to_vm:
            kwargs["path_to_vm"] = self.path_to_vm

        if self.provider == "attach":
            host, port = self._parse_endpoint()
            _install_attach_provider(dm, host, port)
            # attach rides the docker "clean" path (no revert) at the endpoint
            kwargs["provider_name"] = "docker"
            kwargs["path_to_vm"] = host
        elif self.provider == "aws" and self.snapshot_name == "init_state":
            # AWS snapshots ARE the AMI: resolve it for the region/screen size.
            try:
                from desktop_env.providers.aws.config import IMAGE_ID_MAP
                kwargs["snapshot_name"] = IMAGE_ID_MAP[self.region][tuple(self.screen_size)]
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"aws: could not resolve the AMI for region {self.region!r} at "
                    f"{self.screen_size} ({exc}). Pass an explicit --snapshot-name "
                    "(the AMI id) or use a supported region.") from exc

        desktop = DesktopEnv(**kwargs)
        obs = desktop.reset(task_config=task.metadata["task_config"])
        env = OSWorldEnv(task, desktop, self.provider)
        env._last_obs = obs
        return env

    # -- scoring (official evaluate) -------------------------------------
    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        assert isinstance(env, OSWorldEnv)
        base = dict(task_id=task.id, benchmark=self.name, agent="",
                    cost=trajectory.cost, wall_time=trajectory.wall_time)
        try:
            reward = float(env.desktop.evaluate())
        except Exception as exc:  # noqa: BLE001 - report eval failures faithfully
            return Result(**base, success=False, score=0.0,
                          error=f"osworld evaluate() failed: {exc}",
                          metrics={"actions": env.n_actions,
                                   "domain": task.metadata.get("domain"),
                                   "provider": env.provider})
        success = reward >= 1.0
        return Result(
            **base, success=success, score=reward,
            metrics={"reward": reward, "actions": env.n_actions,
                     "domain": task.metadata.get("domain"),
                     "provider": env.provider})
