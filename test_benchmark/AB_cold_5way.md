# GDPval 5-way — baseline vs pure LLM+files vs FBL v2/v3/v4

Same 100 gdpval train tasks, same attachment file-I/O, same Gemini generator + grader (rotation proxy). Only the scaffolding around the generator changes.

- **baseline** `ace_gdpval` — single playbook, gen→reflect→curator, JSON `final_answer` (concise) graded.
- **pure+files** — one raw LLM call, attachments injected, complete deliverable, no scaffold. (the ceiling)
- **FBL v2** `ace_dual_gdpval` — dual playbook + Thompson + 2 curators; playbook grew a harmful "I am unable to generate binary files…" preamble.
- **FBL v3** — after cutting that disclaimer preamble (seed/prompt edits).
- **FBL v4** — biggest revision: RAW deliverable output (no JSON envelope) + `CITED:` marker, completeness/enumeration rules (rb-fmt-07/chk-02 + prompt), **robust grader** (temperature 0 + retry, kills spurious 0s), causal/counterfactual bullet tagging + harmful-floor counting, curator single-mode fix, deduped rulebook.

> **Caveat (fairness):** FBL v4 was graded with the *robust* grader (temperature 0 + retry); baseline/pure/v2/v3 used the older grader, which occasionally returns an unparseable verdict and scores a good deliverable 0. So part of v4's gain is noise removal that would also lift the others; a fully fair comparison re-grades every run with the robust grader.

## 1. Overall scores

| metric | baseline | pure+files | FBL v2 | FBL v3 | FBL v4 |
|---|---|---|---|---|---|
| **mean score** | 0.5306 | **0.6459** 🥇 | 0.6029 | 0.6099 | 0.6306 |
| pass ≥0.5 | 63 | 75 | 69 | 70 | **76** 🥇 |
| good ≥0.8 | 13 | 32 | 26 | 30 | 27 |
| zeros | 8 | 5 | 5 | 3 | 5 |
| avg deliverable chars | 2090 | 3856 | 3486 | 3792 | 3630 |

**FBL progression** (gap to pure): v2 0.603 (−0.043) → v3 0.610 (−0.036) → **v4 0.631 (−0.015)**.

Head-to-head: v4 vs v3 46–40 (tie 14), v4 vs v2 53–35, v4 vs pure 32–49. **v4's pass-rate (76) is the highest of all, beating pure (75).**

## 2. By sector (mean score)

| sector | n | baseline | pure+files | FBL v2 | FBL v3 | FBL v4 |
|---|---|---|---|---|---|---|
| Retail Trade | 8 | 0.706 | 0.750 | **0.777** | 0.723 | 0.724 |
| Professional, Scientific, and Technical Services | 12 | 0.513 | **0.685** | 0.643 | 0.617 | 0.661 |
| Health Care and Social Assistance | 10 | 0.543 | **0.681** | 0.600 | 0.637 | 0.671 |
| Government | 13 | 0.676 | 0.673 | 0.638 | 0.685 | **0.701** |
| Information | 13 | 0.520 | 0.670 | 0.571 | 0.652 | **0.688** |
| Real Estate and Rental and Leasing | 12 | 0.564 | **0.652** | 0.605 | 0.642 | 0.592 |
| Finance and Insurance | 11 | 0.541 | 0.648 | 0.597 | 0.584 | **0.667** |
| Manufacturing | 11 | 0.383 | 0.535 | **0.583** | 0.446 | 0.468 |
| Wholesale Trade | 10 | 0.334 | **0.527** | 0.441 | 0.499 | 0.499 |

## 3. Token usage by role (calls + tokens)

Brain roles = **exact** (`detailed_llm_logs`, windowed per run by completion time). `grader*` and pure's `llm-call*` = **reconstructed** (lengths → chars/4, Gemini-approx; not logged).

### baseline
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 1,207,501 | 104,198 | 1,311,699 |
| reflector | 100 | 471,505 | 54,929 | 526,434 |
| curator | 100 | 1,209,902 | 48,331 | 1,258,233 |
| grader* | 100 | 371,811 | 69,360 | 441,171 |
| **TOTAL** | **400** | **3,260,719** | **276,818** | **3,537,537** |

### pure+files
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| llm-call* | 100 | 99,489 | 96,373 | 195,862 |
| grader* | 100 | 415,943 | 69,360 | 485,303 |
| **TOTAL** | **200** | **515,432** | **165,733** | **681,165** |

### FBL v2
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 1,353,185 | 145,093 | 1,498,278 |
| reflector | 120 | 689,439 | 80,694 | 770,133 |
| curator | 120 | 1,214,131 | 46,543 | 1,260,674 |
| grader* | 100 | 406,705 | 69,360 | 476,065 |
| **TOTAL** | **440** | **3,663,460** | **341,690** | **4,005,150** |

### FBL v3
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 1,441,785 | 168,898 | 1,610,683 |
| reflector | 120 | 814,172 | 95,181 | 909,353 |
| curator | 120 | 1,299,667 | 44,200 | 1,343,867 |
| grader* | 100 | 414,338 | 69,360 | 483,698 |
| **TOTAL** | **440** | **3,969,962** | **377,639** | **4,347,601** |

### FBL v4
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 1,061,972 | 121,866 | 1,183,838 |
| reflector | 100 | 762,846 | 47,662 | 810,508 |
| curator | 100 | 1,021,930 | 28,785 | 1,050,715 |
| grader* | 100 | 410,282 | 69,360 | 479,642 |
| **TOTAL** | **400** | **3,257,030** | **267,673** | **3,524,703** |

### Token summary
| | baseline | pure+files | FBL v2 | FBL v3 | FBL v4 |
|---|---|---|---|---|---|
| total calls | 400 | 200 | 440 | 440 | 400 |
| total tokens | 3,537,537 | 681,165 | 4,005,150 | 4,347,601 | 3,524,703 |

## 4. All 100 tasks — score line-up (dataset order)

Winner **bolded**. `Δv4−v3` = effect of the v4 revision per task.

| # | task_id | sector | baseline | pure | v2 | v3 | v4 | Δv4−v3 | best |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 83d10b06 | Professional, Sc | 0.000 | 0.270 | 0.444 | 0.429 | **0.476** | +0.048 | v4 |
| 2 | f84ea6ac | Government | **0.845** | 0.741 | 0.793 | **0.845** | 0.793 | -0.052 | baseline |
| 3 | 99ac6944 | Information | 0.256 | **0.841** | 0.549 | 0.598 | 0.524 | -0.073 | pure |
| 4 | 1b1ade2d | Manufacturing | 0.449 | **0.899** | 0.638 | 0.667 | 0.797 | +0.130 | pure |
| 5 | 575f8679 | Government | 0.631 | **0.908** | 0.738 | 0.892 | 0.862 | -0.031 | pure |
| 6 | 36d567ba | Government | **0.741** | **0.741** | **0.741** | **0.741** | **0.741** | +0.000 | baseline |
| 7 | cebf301e | Professional, Sc | 0.855 | **0.968** | 0.887 | 0.952 | 0.935 | -0.016 | pure |
| 8 | a10ec48c | Real Estate and  | 0.543 | 0.600 | 0.600 | **0.714** | 0.600 | -0.114 | v3 |
| 9 | a0ef404e | Real Estate and  | 0.707 | 0.741 | 0.759 | 0.759 | **0.810** | +0.052 | v4 |
| 10 | f3351922 | Finance and Insu | 0.518 | 0.753 | **0.765** | 0.506 | 0.612 | +0.106 | v2 |
| 11 | 401a07f1 | Information | **0.939** | **0.939** | **0.939** | 0.848 | **0.939** | +0.091 | baseline |
| 12 | 8c8fc328 | Information | 0.882 | 0.902 | 0.882 | 0.863 | **1.000** | +0.137 | v4 |
| 13 | a1963a68 | Finance and Insu | 0.606 | 0.697 | 0.621 | 0.606 | **0.758** | +0.152 | v4 |
| 14 | 8079e27d | Finance and Insu | **0.418** | 0.182 | 0.182 | 0.164 | 0.164 | +0.000 | baseline |
| 15 | ec591973 | Wholesale Trade | 0.429 | **0.571** | 0.558 | 0.545 | **0.571** | +0.026 | pure |
| 16 | 6dcae3f5 | Health Care and  | **0.000** | **0.000** | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 17 | 8c823e32 | Government | 0.770 | **0.865** | 0.833 | 0.810 | 0.825 | +0.016 | pure |
| 18 | bf68f2ad | Manufacturing | 0.018 | 0.554 | **0.732** | 0.429 | 0.607 | +0.179 | v2 |
| 19 | bd72994f | Retail Trade | **0.828** | 0.793 | **0.828** | 0.724 | 0.793 | +0.069 | baseline |
| 20 | 8f9e8bcd | Retail Trade | **0.959** | 0.939 | **0.959** | 0.939 | 0.939 | +0.000 | baseline |
| 21 | 8a7b6fca | Manufacturing | 0.675 | 0.000 | **0.925** | 0.050 | 0.050 | +0.000 | v2 |
| 22 | cd9efc18 | Professional, Sc | 0.650 | **0.821** | 0.814 | 0.657 | 0.793 | +0.136 | pure |
| 23 | 5e2b6aab | Manufacturing | 0.294 | **1.000** | 0.426 | 0.368 | 0.426 | +0.059 | pure |
| 24 | f1be6436 | Health Care and  | 0.365 | 0.770 | 0.365 | 0.541 | **0.946** | +0.405 | v4 |
| 25 | 74d6e8b0 | Health Care and  | 0.188 | **0.406** | 0.275 | 0.217 | 0.333 | +0.116 | pure |
| 26 | 60221cd0 | Information | 0.690 | **0.793** | 0.655 | 0.759 | 0.690 | -0.069 | pure |
| 27 | 1a78e076 | Health Care and  | 0.541 | 0.753 | 0.788 | **0.812** | 0.729 | -0.082 | v3 |
| 28 | b5d2e6f1 | Wholesale Trade | 0.000 | 0.492 | 0.046 | **0.738** | 0.523 | -0.215 | v3 |
| 29 | 9a0d8d36 | Finance and Insu | 0.712 | **0.827** | 0.596 | 0.404 | 0.788 | +0.385 | pure |
| 30 | 91060ff0 | Retail Trade | 0.636 | **0.855** | 0.745 | **0.855** | 0.836 | -0.018 | pure |
| 31 | ae0c1093 | Retail Trade | 0.636 | **0.682** | **0.682** | **0.682** | **0.682** | +0.000 | pure |
| 32 | 6241e678 | Information | 0.577 | 0.939 | **0.963** | 0.595 | 0.521 | -0.074 | v2 |
| 33 | 02aa1805 | Professional, Sc | 0.826 | **0.977** | 0.814 | 0.930 | 0.651 | -0.279 | pure |
| 34 | a99d85fc | Real Estate and  | 0.610 | **0.961** | 0.753 | 0.948 | 0.675 | -0.273 | pure |
| 35 | 46bc7238 | Real Estate and  | 0.806 | 0.776 | 0.791 | 0.821 | **0.881** | +0.060 | v4 |
| 36 | 5ad0c554 | Real Estate and  | 0.365 | **0.492** | 0.429 | 0.254 | 0.222 | -0.032 | pure |
| 37 | 403b9234 | Government | **0.796** | **0.796** | **0.796** | **0.796** | **0.796** | +0.000 | baseline |
| 38 | 0ec25916 | Health Care and  | 0.776 | 0.836 | **0.851** | 0.806 | 0.821 | +0.015 | v2 |
| 39 | b3573f20 | Wholesale Trade | 0.730 | **0.784** | 0.730 | 0.676 | 0.622 | -0.054 | pure |
| 40 | ab81b076 | Wholesale Trade | 0.327 | **0.731** | 0.385 | 0.423 | 0.635 | +0.212 | pure |
| 41 | b57efde3 | Wholesale Trade | 0.173 | 0.027 | 0.160 | **0.213** | 0.000 | -0.213 | v3 |
| 42 | 9efbcd35 | Finance and Insu | 0.474 | 0.566 | 0.566 | 0.487 | **0.605** | +0.118 | v4 |
| 43 | 5349dd7b | Manufacturing | **0.000** | **0.000** | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 44 | 0e386e32 | Professional, Sc | 0.269 | **0.551** | 0.462 | 0.154 | 0.295 | +0.141 | pure |
| 45 | 7b08cd4d | Professional, Sc | 0.663 | 0.674 | 0.607 | 0.562 | **0.685** | +0.124 | v4 |
| 46 | a328feea | Government | 0.917 | 0.917 | 0.750 | **1.000** | 0.917 | -0.083 | v3 |
| 47 | f9a1c16c | Information | 0.747 | 0.722 | 0.684 | 0.671 | **0.962** | +0.291 | v4 |
| 48 | 93b336f3 | Manufacturing | 0.553 | 0.724 | 0.803 | **0.895** | 0.763 | -0.132 | v3 |
| 49 | a74ead3b | Government | **0.635** | 0.435 | 0.306 | 0.306 | 0.412 | +0.106 | baseline |
| 50 | 7bbfcfe9 | Government | **0.283** | 0.208 | 0.151 | 0.151 | 0.151 | +0.000 | baseline |
| 51 | c2e8f271 | Professional, Sc | 0.407 | **0.568** | 0.519 | 0.519 | 0.556 | +0.037 | pure |
| 52 | fccaa4a1 | Real Estate and  | 0.611 | 0.704 | 0.611 | **0.722** | 0.704 | -0.019 | v3 |
| 53 | b7a5912e | Real Estate and  | 0.325 | **0.579** | 0.439 | 0.447 | 0.412 | -0.035 | pure |
| 54 | 61717508 | Finance and Insu | 0.734 | 0.891 | 0.766 | 0.891 | **0.906** | +0.016 | v4 |
| 55 | afe56d05 | Information | 0.556 | **0.632** | **0.632** | 0.590 | 0.564 | -0.026 | pure |
| 56 | e222075d | Information | 0.000 | 0.000 | 0.000 | 0.738 | **0.885** | +0.148 | v4 |
| 57 | 5f6c57dd | Finance and Insu | **0.000** | **0.000** | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 58 | e21cd746 | Finance and Insu | 0.590 | 0.821 | 0.667 | **0.846** | **0.846** | +0.000 | v3 |
| 59 | 62f04c2f | Wholesale Trade | 0.857 | **0.934** | 0.846 | 0.868 | 0.791 | -0.077 | pure |
| 60 | 1aecc095 | Health Care and  | 0.689 | 0.767 | 0.467 | **0.789** | 0.656 | -0.133 | v3 |
| 61 | eb54f575 | Government | 0.651 | 0.778 | 0.746 | **0.841** | 0.762 | -0.079 | v3 |
| 62 | efca245f | Manufacturing | 0.118 | 0.245 | **0.294** | 0.275 | 0.275 | +0.000 | v2 |
| 63 | 211d0093 | Retail Trade | 0.882 | 0.549 | **0.922** | 0.549 | 0.569 | +0.020 | v2 |
| 64 | 0fad6023 | Retail Trade | 0.545 | 0.727 | 0.576 | **0.803** | 0.727 | -0.076 | v3 |
| 65 | 40a99a31 | Manufacturing | 0.423 | 0.701 | 0.701 | 0.598 | **0.753** | +0.155 | v4 |
| 66 | a97369c7 | Professional, Sc | 0.603 | **0.865** | 0.643 | 0.651 | 0.683 | +0.032 | pure |
| 67 | 46fc494e | Manufacturing | 0.650 | 0.427 | **0.812** | 0.462 | 0.436 | -0.026 | v2 |
| 68 | 41f6ef59 | Health Care and  | 0.848 | **0.879** | 0.848 | 0.773 | 0.788 | +0.015 | pure |
| 69 | 81db15ff | Health Care and  | 0.710 | 0.710 | **0.783** | 0.696 | 0.710 | +0.014 | v2 |
| 70 | ef8719da | Information | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** | +0.000 | baseline |
| 71 | 1b9ec237 | Health Care and  | 0.746 | **0.881** | 0.797 | **0.881** | 0.864 | -0.017 | pure |
| 72 | f841ddcf | Wholesale Trade | 0.341 | 0.682 | 0.466 | **0.773** | 0.625 | -0.148 | v3 |
| 73 | 664a42e5 | Finance and Insu | 0.760 | **0.940** | 0.820 | **0.940** | 0.840 | -0.100 | pure |
| 74 | 8384083a | Retail Trade | 0.524 | 0.762 | **0.810** | 0.746 | 0.698 | -0.048 | v2 |
| 75 | f9f82549 | Retail Trade | 0.636 | **0.697** | **0.697** | 0.485 | 0.545 | +0.061 | pure |
| 76 | e14e32ba | Information | 0.552 | **0.586** | 0.448 | 0.517 | **0.586** | +0.069 | pure |
| 77 | fd6129bd | Professional, Sc | 0.721 | **0.860** | 0.756 | 0.837 | 0.791 | -0.047 | pure |
| 78 | 55ddb773 | Real Estate and  | 0.304 | 0.321 | 0.321 | **0.429** | 0.232 | -0.196 | v3 |
| 79 | 2d06bc0a | Real Estate and  | 0.758 | **0.879** | 0.803 | 0.833 | 0.864 | +0.030 | pure |
| 80 | 11593a50 | Real Estate and  | **0.283** | **0.283** | 0.151 | 0.151 | 0.189 | +0.038 | baseline |
| 81 | 1bff4551 | Government | 0.558 | **0.721** | 0.651 | 0.698 | **0.721** | +0.023 | pure |
| 82 | 116e791e | Health Care and  | 0.562 | 0.812 | 0.828 | **0.859** | **0.859** | +0.000 | v3 |
| 83 | a69be28f | Wholesale Trade | 0.198 | 0.178 | **0.208** | **0.208** | 0.188 | -0.020 | v2 |
| 84 | d7cfae6f | Wholesale Trade | 0.198 | 0.619 | 0.825 | 0.246 | **0.841** | +0.595 | v4 |
| 85 | 15d37511 | Wholesale Trade | 0.092 | 0.248 | 0.183 | **0.303** | 0.193 | -0.110 | v3 |
| 86 | 1d4672c8 | Finance and Insu | 0.542 | 0.814 | 0.814 | **0.932** | 0.915 | -0.017 | v3 |
| 87 | a4a9195c | Manufacturing | 0.387 | **0.597** | 0.419 | 0.468 | 0.500 | +0.032 | pure |
| 88 | 7de33b48 | Professional, Sc | 0.683 | 0.833 | 0.817 | 0.833 | **0.850** | +0.017 | v4 |
| 89 | 7d7fc9a7 | Professional, Sc | 0.484 | 0.484 | 0.716 | 0.621 | **0.779** | +0.158 | v4 |
| 90 | 27e8912c | Government | 0.736 | 0.717 | 0.623 | 0.623 | **0.792** | +0.170 | v4 |
| 91 | 38889c3b | Information | 0.161 | 0.065 | **0.323** | 0.258 | 0.000 | -0.258 | v2 |
| 92 | 15ddd28d | Manufacturing | 0.649 | **0.737** | 0.667 | 0.702 | 0.544 | -0.158 | pure |
| 93 | bbe0a93b | Government | 0.790 | 0.457 | 0.926 | **0.963** | 0.951 | -0.012 | v3 |
| 94 | 2696757c | Government | 0.439 | **0.463** | 0.244 | 0.244 | 0.390 | +0.146 | pure |
| 95 | 2ea2e5b5 | Professional, Sc | 0.000 | 0.353 | 0.235 | 0.259 | **0.435** | +0.176 | v4 |
| 96 | f5d428fd | Real Estate and  | 0.774 | 0.811 | 0.830 | **0.887** | 0.792 | -0.094 | v3 |
| 97 | aa071045 | Real Estate and  | 0.686 | 0.674 | **0.767** | 0.744 | 0.721 | -0.023 | v2 |
| 98 | 0ed38524 | Finance and Insu | 0.596 | 0.635 | 0.769 | 0.654 | **0.904** | +0.250 | v4 |
| 99 | 9a8c8e28 | Information | 0.397 | 0.411 | 0.344 | **0.510** | 0.397 | -0.113 | v3 |
| 100 | c94452e4 | Information | 0.000 | **0.875** | 0.000 | 0.536 | **0.875** | +0.339 | pure |

_(tied-)best count: pure 44, v4 32, v3 31, v2 25, baseline 14 / 100._
