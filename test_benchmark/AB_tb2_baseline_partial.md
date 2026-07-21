# terminal_bench cause analysis — tb2_baseline_40 (ace_terminal)

**3/14 passed (21%)**

| task | diff | verdict | tests | cmds | signals | root cause | fix |
|---|---|---|---|---|---|---|---|
| extract-moves-from-video | ? | FAIL | None/None | None | setup_error | — | — |
| feal-differential-cryptanalysis | ? | FAIL | None/None | None | setup_error | — | — |
| cobol-modernization | easy | FAIL | 2/3 | 38 | output_truncated×2 | — | — |
| circuit-fibsqrt | hard | FAIL | 2/3 | 50 | step_budget, output_truncated×1 | — | — |
| adaptive-rejection-sampler | medium | FAIL | 8/9 | 18 | output_truncated×1 | — | — |
| break-filter-js-from-html | medium | FAIL | 0/1 | 41 | output_truncated×1 | — | — |
| build-cython-ext | medium | FAIL | 9/11 | 41 | output_truncated×5 | — | — |
| build-pov-ray | medium | FAIL | 0/3 | 50 | step_budget, bash_timeout×2, high_error_rate(27/50) | — | — |
| caffe-cifar-10 | medium | FAIL | 1/6 | 35 | bash_timeout×29, output_truncated×1 | — | — |
| chess-best-move | medium | FAIL | 0/1 | 22 | output_truncated×1 | — | — |
| code-from-image | medium | FAIL | 1/2 | 43 | — | — | — |
| bn-fit-modify | hard | PASS | 9/9 | 36 | output_truncated×2 | — | — |
| cancel-async-tasks | hard | PASS | 6/6 | 5 | — | — | — |
| build-pmars | medium | PASS | 4/4 | 22 | output_truncated×1 | — | — |

## Harness / tool limits (actionable without touching the agent)

- `output_truncated` — 9 task(s)
- `step_budget` — 2 task(s)
- `bash_timeout` — 2 task(s)
- `setup_error` — 2 task(s)
- `high_error_rate` — 1 task(s)
