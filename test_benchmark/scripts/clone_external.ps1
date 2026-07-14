# Clone the upstream benchmark repos into external/ (idempotent).
#
#   powershell -ExecutionPolicy Bypass -File scripts/clone_external.ps1
#
# Each real benchmark reads tasks/scoring from its upstream checkout under
# external/<repo>. GDPval has no repo (it loads the openai/gdpval HF dataset).
# Run from the project root (C:/GemmyStudioSummer/test_benchmark). Existing clones are skipped;
# pass -Update to `git pull` them instead.

param([switch]$Update)

$ErrorActionPreference = "Stop"
$repos = @(
    @{ dir = "OSWorld";         url = "https://github.com/xlang-ai/OSWorld.git" }
    @{ dir = "SWE-bench";       url = "https://github.com/swe-bench/SWE-bench.git" }
    @{ dir = "terminal-bench-2"; url = "https://github.com/laude-institute/terminal-bench-2.git" }  # Terminal-Bench 2.0 tasks
    @{ dir = "tau-bench";       url = "https://github.com/sierra-research/tau-bench.git" }
    @{ dir = "AutomationBench"; url = "https://github.com/zapier/AutomationBench.git" }
    @{ dir = "simple-evals";    url = "https://github.com/openai/simple-evals.git" }  # BrowseComp
)

New-Item -ItemType Directory -Force -Path "external" | Out-Null
foreach ($r in $repos) {
    $path = Join-Path "external" $r.dir
    if (Test-Path $path) {
        if ($Update) { Write-Host "updating $path"; git -C $path pull --ff-only }
        else { Write-Host "skip (exists): $path" }
    } else {
        Write-Host "cloning $($r.url) -> $path"
        git clone --depth 1 $r.url $path
    }
}
Write-Host "`nDone. GDPval needs no clone (loads the openai/gdpval HF dataset)."
Write-Host "Install per-benchmark deps with the matching extra, e.g.:"
Write-Host '  uv pip install -e ".[tau_bench]"   # then .[automation_bench], etc.'
Write-Host '  cd external/OSWorld; pip install -e .   # OSWorld has heavy deps'
