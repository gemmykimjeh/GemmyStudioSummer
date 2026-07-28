# GDPval FULL 220 — 4-arm comparison

baseline ACE, pure LLM+tools, rulebook-only, FBL v6.3 — same 220 tasks, same file-I/O + codegen pipeline, same grader (rotation proxy). All data integrity-checked (220 unique each, 0 duplicates, no quota-corruption).

**Split that matters:** every FBL version (v2→v6.3) and the rulebook were iterated against the FIRST 100 tasks. Tasks 101–220 are effectively HELD-OUT — never seen during development. If an arm's ranking holds there, it generalizes; if it collapses, it was overfitting.

## 1. Overall & held-out split

### ALL 220  (n=220)

| metric | baseline | pure+tools | rulebook | FBL v6.3 |
|---|---|---|---|---|
| **mean** | 0.5041 | 0.5729 | 0.5956 | **0.5981** 🥇 |
| pass ≥0.5 | 122 | 146 | 148 | 151 |
| good ≥0.8 | 35 | 62 | 73 | 60 |
| zeros | 20 | 26 | 14 | 12 |

### TUNING (first 100)  (n=100)

| metric | baseline | pure+tools | rulebook | FBL v6.3 |
|---|---|---|---|---|
| **mean** | 0.5214 | 0.5918 | 0.6188 | **0.6221** 🥇 |
| pass ≥0.5 | 57 | 67 | 69 | 72 |
| good ≥0.8 | 15 | 29 | 35 | 28 |
| zeros | 6 | 13 | 4 | 5 |

### HELD-OUT (tasks 101–220)  (n=120)

| metric | baseline | pure+tools | rulebook | FBL v6.3 |
|---|---|---|---|---|
| **mean** | 0.4896 | 0.5571 | 0.5763 | **0.5781** 🥇 |
| pass ≥0.5 | 65 | 79 | 79 | 79 |
| good ≥0.8 | 20 | 33 | 38 | 32 |
| zeros | 14 | 13 | 10 | 7 |

**Generalization check (mean):**

| arm | tuning(1-100) | held-out(101-220) | drop |
|---|---|---|---|
| baseline ACE | 0.5214 | 0.4896 | +0.0318 |
| pure LLM+tools | 0.5918 | 0.5571 | +0.0347 |
| rulebook-only | 0.6188 | 0.5763 | +0.0424 |
| FBL v6.3 | 0.6221 | 0.5781 | +0.0440 |

A large positive drop = that arm did better on the tasks it was tuned against than on fresh ones (overfitting signature). Compare drops across arms: baseline/pure were NOT tuned on these tasks, so their drop is the natural task-difficulty difference between the two halves; any FBL/rulebook drop beyond that baseline drop is fitting.

## 2. Head-to-head on HELD-OUT (the honest ranking)

| pair | wins–losses | Δmean |
|---|---|---|
| baseline ACE vs pure LLM+tools | 33–73 | -0.0675 |
| baseline ACE vs rulebook-only | 31–81 | -0.0867 |
| baseline ACE vs FBL v6.3 | 29–80 | -0.0885 |
| pure LLM+tools vs rulebook-only | 50–50 | -0.0193 |
| pure LLM+tools vs FBL v6.3 | 45–59 | -0.0210 |
| rulebook-only vs FBL v6.3 | 51–52 | -0.0018 |

## 3. By gold deliverable type (ALL 220)

| gold | n | baseline | pure | rulebook | FBL v6.3 |
|---|---|---|---|---|---|
| docx/pdf | 101 | 0.575 | 0.622 | 0.673 | 0.686 |
| xlsx | 62 | 0.373 | 0.418 | 0.444 | 0.427 |
| prose/none | 35 | 0.541 | 0.661 | 0.664 | 0.659 |
| other-file | 22 | 0.487 | 0.642 | 0.558 | 0.580 |

## 4. By sector (ALL 220, mean)

| sector | n | baseline | pure | rulebook | FBL v6.3 |
|---|---|---|---|---|---|
| ? | 1 | 0.000 | 0.595 | 0.726 | 0.655 |
| Retail Trade | 20 | 0.596 | 0.701 | 0.721 | 0.720 |
| Health Care and Social Assistance | 25 | 0.564 | 0.622 | 0.658 | 0.628 |
| Government | 24 | 0.554 | 0.569 | 0.654 | 0.683 |
| Finance and Insurance | 25 | 0.490 | 0.519 | 0.603 | 0.617 |
| Professional, Scientific, and Technical Services | 25 | 0.509 | 0.594 | 0.571 | 0.546 |
| Real Estate and Rental and Leasing | 25 | 0.499 | 0.498 | 0.567 | 0.606 |
| Information | 25 | 0.491 | 0.613 | 0.546 | 0.607 |
| Manufacturing | 25 | 0.436 | 0.577 | 0.542 | 0.551 |
| Wholesale Trade | 25 | 0.438 | 0.489 | 0.521 | 0.451 |

## 5. Caveats

- **Single run per arm** (compute/quota limits). Earlier 3-run repeats on the first 100 showed run-to-run mean sd ≈ 0.003 for rulebook and ≈ 0.013 for FBL (learning adds variance), so treat gaps under ~0.02 as provisional.
- **One model everywhere** — generator, reflector, curator, and grader all resolve to gemini-3.1-flash-lite through the proxy's catch-all route.
- Gold reference files were used only to validate the measuring apparatus, never to shape any agent's behaviour.
