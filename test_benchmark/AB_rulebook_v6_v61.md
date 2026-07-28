# GDPval — rulebook-only vs FBL v6 vs FBL v6.1

Same 100 gdpval train tasks, same agent (`ace_dual_gdpval`), same file-I/O + codegen pipeline, same grader, all cold starts. The ONLY variable is what the generator is shown and what the learning loop may write.

- **rulebook-only** — `ACE_SHOW_PLAYBOOK=0`. The immutable 27-rule rulebook is shown; the LEARNED playbook is hidden from the generator. Learning still runs in the background (reflect, helpful/harmful tagging, counting, prune, curate) — it is just never displayed. This isolates *what the learned playbook adds on top of the rulebook*.
- **FBL v6** — rulebook + learned playbook, with the growth gate (a task scoring above 0.75 still reflects but may not grow the playbook).
- **FBL v6.1** — v6 plus: (a) **rubric-referencing bullets banned** — the generator never sees a rubric, so "treat the rubric as a schema" is an instruction it cannot follow; (b) codegen **respects the script's exit code** (a crash that left a stub file used to count as success); (c) **empty artifacts rejected**.

## 1. Overall scores

| metric | rulebook-only | FBL v6 | FBL v6.1 |
|---|---|---|---|
| **mean score** | **0.6612** 🥇 | 0.6288 | 0.6007 |
| median | 0.735 | 0.695 | 0.698 |
| pass ≥0.5 | 75 | 71 | 68 |
| good ≥0.8 | 35 | 30 | 29 |
| zeros | 3 | 4 | 6 |
| avg deliverable chars | 3654 | 3656 | 3809 |
| criteria met rate | 0.627 | 0.596 | 0.584 |

**Head-to-head**

| pair | wins–losses (ties) | Δmean | p |
|---|---|---|---|
| rulebook-only vs FBL v6 | 43–29 (28) | +0.0324 | 0.099 |
| rulebook-only vs FBL v6.1 | 51–28 (21) | +0.0605 | 0.010 |
| FBL v6 vs FBL v6.1 | 42–34 (24) | +0.0280 | 0.359 |

## 2. Reproducibility — how much of the gap is run-to-run variation?

The right way to size the noise is to run the SAME configuration twice and see how much it moves on its own. Both arms were repeated (`*_r2` runs); the difference between two identical runs is pure variation, because the true effect there is zero.

| arm | run 1 | run 2 | Δ between identical runs | per-task sd | tasks scoring identically |
|---|---|---|---|---|---|
| rulebook-only | 0.6612 | 0.6575 | -0.0037 | 0.052 | 93/100 |
| FBL v6.1 | 0.6007 | 0.5979 | -0.0029 | 0.209 | 31/100 |

**Two things fall out, and they point in opposite directions.**

**(a) The means are stable, so the gap between arms is real.** Each arm reproduces its own mean to within ~0.004. rulebook-only lands at ~0.659 twice; v6.1 lands at ~0.599 twice. The **~0.060 gap between them is roughly 15× the run-to-run wobble**, so it is not something that will vanish on the next run.

**(b) Per-task stability differs enormously — and that is itself a finding.** rulebook-only scores *identically* on 93 of 100 tasks across two runs (sd 0.052): with a fixed 27-rule prompt, the generator behaves almost deterministically. v6.1 repeats only 31 of 100 (sd 0.209, 4× higher). The reason is structural: the learned playbook **grows differently on every run** — reflection and curation are stochastic — so by the middle of a run the two repeats are being shown different prompts. The learning loop's clearest measurable effect is therefore **added variance**, not a better mean. For a deployed agent that is a cost in its own right: the same task stops giving the same answer.

> **Correction.** An earlier version of this document filtered out tasks where the three arms disagreed sharply, calling them "codegen lottery", and concluded the arms were indistinguishable. That was wrong. The repeats above show rulebook-only is nearly deterministic, so disagreement *between arms* was the condition difference — the real signal — not noise. Filtering it discarded the effect being measured.

Caveat: FBL v6 has only one run, so it has no reproducibility estimate here; its 0.6288 should be read as a single sample.

## 3. Mechanism — codegen outcome mix

| arm | file produced | no_code (prose) | model failure | env blocked |
|---|---|---|---|---|
| rulebook-only | 89 | **8** | 2 | 1 |
| FBL v6 | 80 | **15** | 3 | 2 |
| FBL v6.1 | 86 | **8** | 3 | 3 |

`no_code` = the task wanted a file but the model wrote prose. v6's learned playbook pushed the model toward prose (15 cases vs 8); the v6.1 rubric ban removed exactly that pressure and brought it back to 8 — matching rulebook-only. This is a **directional, reproducible** effect (in the v6-vs-rulebook pairing it was 7–0 asymmetric), unlike the score differences above. The mechanism was real and the fix worked — it just did not move the score.

## 4. What the learning loop produced

| arm | final playbook | bullets | rubric-referencing bullets | shown to generator? |
|---|---|---|---|---|
| rulebook-only | 40,368 chars | 104 | 37 | no (hidden) |
| FBL v6 | 42,156 chars | 105 | 62 | yes |
| FBL v6.1 | 43,731 chars | 115 | 0 | yes |

The rubric ban worked: **62 → 0** contaminated bullets. Note the playbook did not shrink — v6.1 ended up with *more* bullets (115) in *more* characters than v6, because the reflector, told not to lean on the rubric, wrote other lessons instead. So v6.1 tests "cleaner content, same volume", not "less content".

## 5. By sector (mean score)

| sector | n | rulebook-only | FBL v6 | FBL v6.1 |
|---|---|---|---|---|
| Retail Trade | 8 | **0.851** | 0.826 | 0.751 |
| Information | 13 | **0.757** | 0.728 | 0.712 |
| Government | 13 | **0.722** | 0.705 | 0.702 |
| Health Care and Social Assistance | 10 | **0.716** | 0.630 | 0.661 |
| Finance and Insurance | 11 | 0.692 | **0.702** | 0.604 |
| Professional, Scientific, and Technical Services | 12 | **0.677** | 0.625 | 0.511 |
| Manufacturing | 11 | 0.540 | 0.525 | **0.560** |
| Real Estate and Rental and Leasing | 12 | 0.535 | **0.587** | 0.519 |
| Wholesale Trade | 10 | **0.484** | 0.330 | 0.392 |

## 6. By gold deliverable type

| gold type | n | rulebook-only | FBL v6 | FBL v6.1 |
|---|---|---|---|---|
| docx/pdf | 50 | 0.726 | 0.686 | 0.673 |
| xlsx | 23 | 0.492 | 0.437 | 0.415 |
| prose / none | 16 | 0.666 | 0.671 | 0.619 |
| other file | 11 | 0.715 | 0.707 | 0.632 |

`xlsx` is the weakest type for every arm (0.42–0.49). Grading GDPval's own gold spreadsheets through this same pipeline caps out at ~0.80, so part of that is the measurement, not the agent.

## 7. Token usage by role

Brain roles are exact (`detailed_llm_logs`, windowed per run by completion time); `grader*` is reconstructed from lengths (chars/4).

### rulebook-only
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 427,662 | 116,171 | 543,833 |
| reflector | 100 | 709,336 | 42,807 | 752,143 |
| curator | 56 | 459,578 | 16,363 | 475,941 |
| grader* | 100 | 410,906 | 69,360 | 480,266 |
| **TOTAL** | **356** | **2,007,482** | **244,701** | **2,252,183** |

### FBL v6
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 959,569 | 114,962 | 1,074,531 |
| reflector | 100 | 739,613 | 44,760 | 784,373 |
| curator | 62 | 552,901 | 18,493 | 571,394 |
| grader* | 100 | 410,951 | 69,360 | 480,311 |
| **TOTAL** | **362** | **2,663,034** | **247,575** | **2,910,609** |

### FBL v6.1
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 915,191 | 123,118 | 1,038,309 |
| reflector | 100 | 776,515 | 43,694 | 820,209 |
| curator | 60 | 537,029 | 16,927 | 553,956 |
| grader* | 100 | 414,781 | 69,360 | 484,141 |
| **TOTAL** | **360** | **2,643,516** | **253,099** | **2,896,615** |

| | rulebook-only | FBL v6 | FBL v6.1 |
|---|---|---|---|
| total calls | 356 | 362 | 360 |
| total tokens | 2,252,183 | 2,910,609 | 2,896,615 |

rulebook-only still pays for the full learning loop (it learns, it just does not display the result), so its cost is not lower — its generator prompts are simply much shorter.

## 8. All 100 tasks

`spread` = max−min across the three arms. Per §2 this is **not** a noise measure — rulebook-only reproduces 93/100 tasks exactly — so a large spread marks a task where the arms genuinely diverge, i.e. where the condition matters most.

| # | task | sector | gold | rulebook-only | FBL v6 | FBL v6.1 | spread |
|---|---|---|---|---|---|---|---|
| 1 | 83d10b06 | Professional,  | xlsx | **0.730** | **0.730** | **0.730** | 0.00 |
| 2 | f84ea6ac | Government | docx | **0.879** | 0.845 | **0.879** | 0.03 |
| 3 | 99ac6944 | Information | pdf | **0.744** | **0.744** | 0.707 | 0.04 |
| 4 | 1b1ade2d | Manufacturing | docx | **0.826** | 0.638 | 0.768 | 0.19 |
| 5 | 575f8679 | Government | - | 0.846 | **0.908** | 0.769 | 0.14 |
| 6 | 36d567ba | Government | docx | **0.778** | 0.741 | 0.741 | 0.04 |
| 7 | cebf301e | Professional,  | docx | **0.919** | **0.919** | **0.919** | 0.00 |
| 8 | a10ec48c | Real Estate an | docx | **0.714** | 0.657 | 0.571 | 0.14 |
| 9 | a0ef404e | Real Estate an | docx | **0.810** | 0.759 | 0.759 | 0.05 |
| 10 | f3351922 | Finance and In | - | **0.765** | 0.706 | 0.682 | 0.08 |
| 11 | 401a07f1 | Information | - | **1.000** | 0.909 | 0.758 | 0.24 |
| 12 | 8c8fc328 | Information | docx | 0.980 | 0.980 | **1.000** | 0.02 |
| 13 | a1963a68 | Finance and In | - | **0.712** | 0.652 | 0.682 | 0.06 |
| 14 | 8079e27d | Finance and In | - | **0.755** | 0.636 | 0.473 | 0.28 |
| 15 | ec591973 | Wholesale Trad | pptx | 0.364 | **0.481** | 0.351 | 0.13 |
| 16 | 6dcae3f5 | Health Care an | docx,xlsx | **0.761** | 0.030 | 0.701 | 0.73 |
| 17 | 8c823e32 | Government | - | 0.817 | 0.817 | **0.897** | 0.08 |
| 18 | bf68f2ad | Manufacturing | xlsx | **0.464** | 0.393 | 0.161 | 0.30 |
| 19 | bd72994f | Retail Trade | - | 0.828 | 0.793 | **0.897** | 0.10 |
| 20 | 8f9e8bcd | Retail Trade | docx | 0.980 | **1.000** | 0.980 | 0.02 |
| 21 | 8a7b6fca | Manufacturing | pdf | 0.425 | 0.425 | **1.000** | 0.57 |
| 22 | cd9efc18 | Professional,  | - | **0.821** | 0.750 | 0.779 | 0.07 |
| 23 | 5e2b6aab | Manufacturing | pdf,zip | 0.588 | **0.706** | 0.088 | 0.62 |
| 24 | f1be6436 | Health Care an | docx | **0.500** | 0.378 | 0.365 | 0.14 |
| 25 | 74d6e8b0 | Health Care an | docx | **0.304** | **0.304** | 0.246 | 0.06 |
| 26 | 60221cd0 | Information | pdf | **0.793** | 0.655 | 0.690 | 0.14 |
| 27 | 1a78e076 | Health Care an | docx | 0.824 | **0.906** | 0.871 | 0.08 |
| 28 | b5d2e6f1 | Wholesale Trad | xlsx | **0.400** | 0.338 | 0.338 | 0.06 |
| 29 | 9a0d8d36 | Finance and In | pptx | **0.769** | 0.692 | 0.577 | 0.19 |
| 30 | 91060ff0 | Retail Trade | - | **0.909** | **0.909** | 0.782 | 0.13 |
| 31 | ae0c1093 | Retail Trade | pdf | **0.818** | **0.818** | 0.727 | 0.09 |
| 32 | 6241e678 | Information | pdf | **0.988** | 0.620 | 0.890 | 0.37 |
| 33 | 02aa1805 | Professional,  | xlsx | 0.651 | 0.651 | **0.733** | 0.08 |
| 34 | a99d85fc | Real Estate an | xlsx | 0.740 | **0.961** | 0.649 | 0.31 |
| 35 | 46bc7238 | Real Estate an | pdf | 0.881 | 0.866 | **0.896** | 0.03 |
| 36 | 5ad0c554 | Real Estate an | docx | **0.254** | 0.143 | 0.143 | 0.11 |
| 37 | 403b9234 | Government | pptx | **0.981** | **0.981** | **0.981** | 0.00 |
| 38 | 0ec25916 | Health Care an | pdf | **0.836** | **0.836** | **0.836** | 0.00 |
| 39 | b3573f20 | Wholesale Trad | pdf | **0.838** | 0.784 | **0.838** | 0.05 |
| 40 | ab81b076 | Wholesale Trad | pdf | 0.481 | **0.596** | 0.538 | 0.12 |
| 41 | b57efde3 | Wholesale Trad | xlsx | 0.200 | **0.213** | 0.160 | 0.05 |
| 42 | 9efbcd35 | Finance and In | - | 0.645 | **0.658** | **0.658** | 0.01 |
| 43 | 5349dd7b | Manufacturing | xlsx | **0.000** | **0.000** | **0.000** | 0.00 |
| 44 | 0e386e32 | Professional,  | zip | **0.577** | 0.538 | 0.154 | 0.42 |
| 45 | 7b08cd4d | Professional,  | xlsx | **0.438** | 0.000 | 0.000 | 0.44 |
| 46 | a328feea | Government | docx | **1.000** | 0.917 | 0.917 | 0.08 |
| 47 | f9a1c16c | Information | pdf | 0.975 | **0.987** | 0.886 | 0.10 |
| 48 | 93b336f3 | Manufacturing | docx | 0.645 | **0.737** | **0.737** | 0.09 |
| 49 | a74ead3b | Government | pptx | **0.412** | **0.412** | **0.412** | 0.00 |
| 50 | 7bbfcfe9 | Government | xlsx | **0.189** | **0.189** | **0.189** | 0.00 |
| 51 | c2e8f271 | Professional,  | docx | 0.469 | **0.531** | 0.395 | 0.14 |
| 52 | fccaa4a1 | Real Estate an | pdf | 0.611 | **0.685** | 0.630 | 0.07 |
| 53 | b7a5912e | Real Estate an | xlsx | 0.035 | **0.605** | 0.053 | 0.57 |
| 54 | 61717508 | Finance and In | pdf | 0.859 | **0.891** | 0.766 | 0.12 |
| 55 | afe56d05 | Information | - | 0.581 | 0.521 | **0.590** | 0.07 |
| 56 | e222075d | Information | mp4 | 0.689 | 0.770 | **0.852** | 0.16 |
| 57 | 5f6c57dd | Finance and In | - | **0.000** | **0.000** | **0.000** | 0.00 |
| 58 | e21cd746 | Finance and In | pdf | 0.769 | **0.872** | 0.821 | 0.10 |
| 59 | 62f04c2f | Wholesale Trad | docx,xlsx | **0.912** | 0.396 | 0.396 | 0.52 |
| 60 | 1aecc095 | Health Care an | docx | 0.689 | 0.600 | **0.778** | 0.18 |
| 61 | eb54f575 | Government | pdf | 0.698 | **0.841** | 0.762 | 0.14 |
| 62 | efca245f | Manufacturing | xlsx | 0.490 | 0.539 | **0.569** | 0.08 |
| 63 | 211d0093 | Retail Trade | pdf | **0.980** | **0.980** | 0.941 | 0.04 |
| 64 | 0fad6023 | Retail Trade | xlsx | **0.712** | 0.697 | **0.712** | 0.02 |
| 65 | 40a99a31 | Manufacturing | pdf,xlsx | 0.742 | 0.753 | **0.784** | 0.04 |
| 66 | a97369c7 | Professional,  | docx | **0.706** | 0.667 | 0.698 | 0.04 |
| 67 | 46fc494e | Manufacturing | pdf,png,py | 0.675 | 0.462 | **0.915** | 0.45 |
| 68 | 41f6ef59 | Health Care an | docx,xlsx | **0.833** | **0.833** | 0.379 | 0.45 |
| 69 | 81db15ff | Health Care an | xlsx | **0.783** | **0.783** | **0.783** | 0.00 |
| 70 | ef8719da | Information | - | **1.000** | **1.000** | **1.000** | 0.00 |
| 71 | 1b9ec237 | Health Care an | pptx | **0.881** | 0.864 | **0.881** | 0.02 |
| 72 | f841ddcf | Wholesale Trad | xlsx | **0.193** | 0.023 | 0.023 | 0.17 |
| 73 | 664a42e5 | Finance and In | pptx | 0.720 | 0.820 | **0.860** | 0.14 |
| 74 | 8384083a | Retail Trade | pdf | **0.857** | 0.714 | 0.397 | 0.46 |
| 75 | f9f82549 | Retail Trade | pdf | **0.727** | 0.697 | 0.576 | 0.15 |
| 76 | e14e32ba | Information | - | **0.655** | **0.655** | 0.586 | 0.07 |
| 77 | fd6129bd | Professional,  | docx,pdf | 0.791 | **0.814** | 0.140 | 0.67 |
| 78 | 55ddb773 | Real Estate an | - | 0.321 | **0.429** | 0.357 | 0.11 |
| 79 | 2d06bc0a | Real Estate an | docx | **0.864** | **0.864** | 0.833 | 0.03 |
| 80 | 11593a50 | Real Estate an | pdf | **0.075** | **0.075** | 0.038 | 0.04 |
| 81 | 1bff4551 | Government | pdf | **0.674** | 0.395 | 0.512 | 0.28 |
| 82 | 116e791e | Health Care an | pdf | 0.750 | **0.766** | **0.766** | 0.02 |
| 83 | a69be28f | Wholesale Trad | pdf | **0.970** | 0.228 | 0.960 | 0.74 |
| 84 | d7cfae6f | Wholesale Trad | xlsx | **0.214** | 0.000 | 0.000 | 0.21 |
| 85 | 15d37511 | Wholesale Trad | xlsx | 0.266 | 0.239 | **0.312** | 0.07 |
| 86 | 1d4672c8 | Finance and In | pdf,xlsx | 0.847 | **0.932** | 0.814 | 0.12 |
| 87 | a4a9195c | Manufacturing | docx | 0.500 | 0.548 | **0.565** | 0.06 |
| 88 | 7de33b48 | Professional,  | zip | **1.000** | 0.967 | **1.000** | 0.03 |
| 89 | 7d7fc9a7 | Professional,  | xlsx | 0.432 | 0.474 | **0.589** | 0.16 |
| 90 | 27e8912c | Government | docx,pdf | **0.774** | **0.774** | 0.698 | 0.08 |
| 91 | 38889c3b | Information | zip | **0.887** | 0.790 | **0.887** | 0.10 |
| 92 | 15ddd28d | Manufacturing | docx | **0.579** | **0.579** | **0.579** | 0.00 |
| 93 | bbe0a93b | Government | pdf | **0.877** | **0.877** | **0.877** | 0.00 |
| 94 | 2696757c | Government | pdf | 0.463 | 0.463 | **0.488** | 0.02 |
| 95 | 2ea2e5b5 | Professional,  | pptx | **0.588** | 0.459 | 0.000 | 0.59 |
| 96 | f5d428fd | Real Estate an | pdf | **0.830** | 0.717 | **0.830** | 0.11 |
| 97 | aa071045 | Real Estate an | docx,xlsx | 0.279 | 0.279 | **0.465** | 0.19 |
| 98 | 0ed38524 | Finance and In | pdf | 0.769 | **0.865** | 0.308 | 0.56 |
| 99 | 9a8c8e28 | Information | pdf | **0.543** | 0.444 | 0.411 | 0.13 |
| 100 | c94452e4 | Information | - | 0.000 | **0.393** | 0.000 | 0.39 |

## 9. Caveats

- **Run counts differ.** rulebook-only and v6.1 have two runs each (§2); FBL v6 has one, so its number is a single sample. A third run of each is in progress.
- **The gap is reproducible, the per-task detail is not.** Means repeat to ~0.004, but v6.1 reproduces only 31/100 individual task scores, so read §8 row-by-row with care — an individual v6.1 cell may differ on the next run even though the column mean will not.
- **Same 100 tasks throughout.** FBL v2→v6.1 were all iterated against these tasks, so none of these numbers is evidence of generalisation. A held-out slice is still owed.
- **One model everywhere.** The proxy's `"*"` catch-all routes every model name — including the grader's `claude-sonnet-4-6` — to `gemini-3.1-flash-lite`. Generator, reflector, curator and grader are the same small model.
- Gold reference files were used only to validate the measuring apparatus (the achievable ceiling ≈0.81), never to shape agent behaviour.
