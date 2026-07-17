# GDPval A/B — Baseline ACE vs Feedback-Loop (FBL)

**Setup (identical for both arms):** GDPval `train` split, first **100 tasks**, model
`gemini-3.1-flash-lite` (free tier, OpenAI-compat), `temperature=0`, dedup threshold `0.85`,
grader also `gemini-3.1-flash-lite` (via LiteLLM proxy). Only the agent differs:
- **Baseline** = `ace_gdpval` (single-playbook ACE, `ReAct` repo)
- **FBL** = `ace_dual_gdpval` (dual-playbook: Thompson selector + auto-distill + verifier + two curators, `ReAct_feedback_loop` repo)

Local/free smoke run — absolute scores are **not** paper-grade; the **relative** comparison
(same tasks, grader, conditions) is what matters.

## Table 1 — Overall

| Metric | Baseline | FBL | Δ |
|---|---:|---:|---:|
| **Mean score** | 0.445 | **0.470** | **+0.025** |
| Median | 0.410 | **0.502** | +0.092 |
| Std dev | 0.345 | **0.273** | −0.072 |
| **Pass rate (≥0.5)** | 41/100 (41%) | **52/100 (52%)** | **+11 pp** |
| **Zero (0.0)** | 22 | **10** | **−12** |
| Perfect (1.0) | 9 | 1 | −8 |
| Criteria met | 1818/4624 (39.3%) | 1992/4607 (43.2%) | +3.9 pp |
| Points earned | 2832/7242 | 3139/7205 | — |
| Required-items OK | 100/100 | 99/100 | −1 |
| Score dist. [0,.2)/[.2,.5)/[.5,.8)/[.8,1] | 28 / 31 / 22 / 19 | 20 / 28 / 39 / 13 | — |
| Segment 1–50 | 0.469 | 0.479 | +0.010 |
| Segment 51–79 | 0.493 | 0.510 | +0.017 |
| **Segment 80–100** | 0.322 | **0.392** | **+0.070** |
| Deliverable size (mean chars) | 1451 | 1576 | +125 |
| Wall time (total) | 53.2 min | 64.4 min | +11.2 min |
| Time / task | 31.9 s | 38.6 s | +6.7 s |
| Cost | $0 | $0 | — |
| Errors | 0 | 0 | — |

**Read:** FBL wins on reliability — pass rate **+11 pp**, zeros **halved (22→10)**, median crosses
the pass line, variance down. It trades peak wins (perfect 9→1) for a much higher floor. The
late segment (80–100), where baseline collapses to 0.322, holds better at 0.392.

## Table 2 — By Sector (domain)

| Sector | n | Baseline | FBL | Δ | |
|---|---:|---:|---:|---:|:--:|
| Professional, Scientific & Technical | 12 | 0.210 | **0.420** | **+0.209** | ▲ |
| Information | 13 | 0.447 | **0.576** | **+0.129** | ▲ |
| Retail Trade | 8 | 0.591 | 0.625 | +0.034 | ▲ |
| Real Estate & Rental/Leasing | 12 | 0.457 | 0.471 | +0.014 | ▲ |
| Manufacturing | 11 | 0.363 | 0.374 | +0.010 | ▲ |
| Finance & Insurance | 11 | 0.440 | 0.421 | −0.019 | ▼ |
| Health Care & Social Assistance | 10 | 0.559 | 0.517 | −0.042 | ▼ |
| Wholesale Trade | 9 | 0.347 | 0.288 | −0.059 | ▼ |
| Government | 13 | 0.622 | 0.559 | −0.063 | ▼ |
| (unknown, incomplete metrics) | 1 | 0.00 | 0.00 | — | |
| **Total** | 100 | 0.445 | 0.469 | +0.024 | |

**Read:** FBL's biggest gains are in baseline's **weakest** domains — Professional/Scientific
nearly **doubles** (0.210→0.420) — while giving up a little in already-strong ones (Government
0.622→0.559). "Raise the floor," not "raise the ceiling." 5 sectors up / 4 down, but the up-deltas
are far larger.

## Table 3 — Token usage by role

*ACE-brain roles (generator/reflector/curator/verifier) measured from detailed logs.
Grader = reconstructed estimate (adapter-internal + official rubric grader, ≈2 calls/task, ±20%).*

### Baseline (`ace_gdpval`)
| Role | Calls | Input | Output | Total |
|---|---:|---:|---:|---:|
| generator | 100 | 1.12M | 0.09M | 1.22M |
| reflector | 100 | 0.51M | 0.07M | 0.58M |
| curator | 100 | 1.23M | 0.04M | 1.27M |
| grader (est.) | ~200 | 0.84M | 0.07M | 0.90M |
| **Total** | **~500** | **3.70M** | **0.27M** | **≈ 3.97M** |

### FBL (`ace_dual_gdpval`)
| Role | Calls | Input | Output | Total |
|---|---:|---:|---:|---:|
| generator | 110 | 0.55M | 0.09M | 0.64M |
| reflector | 306 | 1.36M | 0.17M | 1.53M |
| curator | 203 | 1.10M | 0.05M | 1.15M |
| **verifier** | 102 | 1.17M | 0.03M | 1.19M |
| grader (est.) | ~200 | 0.84M | 0.07M | 0.90M |
| **Total** | **~921** | **5.01M** | **0.40M** | **≈ 5.41M** |

**Cost of the gain:** FBL uses **+36% tokens** (5.41M vs 3.97M) and **~1.8× calls** (~9.2 vs ~5 per
task). The extra spend is the dual loop — ~3 reflections + 2 curators + a verifier per task vs
baseline's generate→reflect→curate. Input tokens dominate both (~93%): prefill-bound (playbook +
rubric injection). Free tier → $0, but ≈500–920 requests/run hits the **500 requests/day/model** cap.

## Playbook growth

| | Baseline (single) | FBL (dual: concrete + abstract) |
|---|---|---|
| **Start** | **empty (0 bullets)** | **~21 packaged seed bullets** (10 concrete + 11 abstract, ~4.4 KB, *immutable/protected*) |
| **End (after 100 tasks)** | **217 bullets / ~89 KB** | **~96 bullets / ~32 KB** (concrete 49 + abstract 47) |
| Net grown | +217 bullets | +~75 bullets (from seed) |
| Growth milestones (chars) | t10 ~9 KB · t50 ~45 KB · t100 ~89 KB | (grew to ~32 KB total) |

**Read:** FBL ends **~2.3× leaner** (96 vs 217 bullets) and *grew* far less (+75 vs +217) despite
equal dedup — its verifier + two curators + auto-distill curate more aggressively → a smaller,
cleaner playbook = less context bloat, matching FBL's smaller late-segment collapse and lower variance.

> **⚠️ Not a clean ablation:** the two arms differ in **two** ways, not one — (1) the FBL mechanism
> AND (2) the **starting state** (FBL loads ~21 hand-curated, protected seed bullets; baseline starts
> empty). Part of FBL's "raise-the-floor" gain (esp. in weak domains) may come from the seed, not the
> feedback loop alone. To isolate the loop, seed **both** arms with the same 21 bullets, or run FBL
> **from empty**, and re-compare.

## Bottom line
FBL doesn't make the mean explode (+0.025). What it does is **cut catastrophic failures in half**,
lift pass rate **11 pp**, push the median over the pass line, and keep a **much leaner playbook** —
i.e. a **reliability/consistency win**, concentrated in the domains baseline handled worst, at the
cost of ~36% more tokens.

**Caveats:** (1) **Confounded** — FBL also starts from ~21 protected seed bullets while baseline
starts empty, so the gain is *FBL-mechanism + seed*, not the loop in isolation (see Playbook growth).
(2) Single seed / 100 tasks / weak shared model → directionally consistent but not statistically
conclusive. A clean run would give both arms the same starting playbook (or none) and repeat over
multiple seeds.
