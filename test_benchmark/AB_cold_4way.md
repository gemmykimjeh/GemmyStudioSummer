# GDPval 4-way — baseline vs pure LLM+files vs FBL v3 vs FBL v4

All four share the **same 100 gdpval train tasks, the same attachment file-I/O (xlsx/pdf/docx + image vision), the same Gemini `gemini-3.1-flash-lite` generator, and the same Gemini rubric grader** (via the rotation proxy). The only thing that changes is the **scaffolding around the generator**:

- **baseline (ACE)** `ace_gdpval` — gen→reflect→curator, single playbook; only the JSON `final_answer` (asked *concise*) is graded.
- **pure+files** — one raw LLM call, attachments injected, asked for the complete deliverable. No playbook, no JSON wrapper. (the ceiling)
- **FBL v3** `ace_dual_gdpval` — dual playbook + Thompson + 2 curators; the playbook it grew contained a *harmful* mandatory "I am unable to generate binary files…" preamble bullet.
- **FBL v4** — same, after editing the seeds/prompts to cut that disclaimer preamble.

## 1. Overall scores

| metric | baseline | pure+files | FBL v3 | FBL v4 |
|---|---|---|---|---|
| **mean score** | 0.5306 | **0.6459** 🥇 | 0.6029 | 0.6099 |
| pass ≥0.5 | 63 | 75 | 69 | 70 |
| good ≥0.8 | 13 | 32 | 26 | 30 |
| zeros | 8 | 5 | 5 | 3 |
| avg deliverable chars | 2090 | 3856 | 3486 | 3792 |

- **v4 vs v3**: v4 47 wins / v3 38 / tie 15  (the edit helped, net +).  •  vs pure: v4 loses 54, wins 32, tie 14.

> Ranking: **pure 0.646 > v4 0.610 > v3 0.603 > baseline 0.531**. Scaffolding still costs vs pure, but v4 narrows the gap to −0.036 (v3 was −0.043).

## 2. By sector (mean score)

| sector | n | baseline | pure+files | FBL v3 | FBL v4 |
|---|---|---|---|---|---|
| Retail Trade | 8 | 0.706 | 0.750 | **0.777** | 0.723 |
| Professional, Scientific, and Technical Services | 12 | 0.513 | **0.685** | 0.643 | 0.617 |
| Health Care and Social Assistance | 10 | 0.543 | **0.681** | 0.600 | 0.637 |
| Government | 13 | 0.676 | 0.673 | 0.638 | **0.685** |
| Information | 13 | 0.520 | **0.670** | 0.571 | 0.652 |
| Real Estate and Rental and Leasing | 12 | 0.564 | **0.652** | 0.605 | 0.642 |
| Finance and Insurance | 11 | 0.541 | **0.648** | 0.597 | 0.584 |
| Manufacturing | 11 | 0.383 | 0.535 | **0.583** | 0.446 |
| Wholesale Trade | 10 | 0.334 | **0.527** | 0.441 | 0.499 |

## 3. Token usage by role (calls + tokens)

Brain roles = **exact** (`detailed_llm_logs`, windowed per run by completion time). `grader*` and pure's `llm-call*` = **reconstructed** (lengths → chars/4, Gemini-approx; not logged).

### baseline (ACE)
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

### FBL v3
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 1,353,185 | 145,093 | 1,498,278 |
| reflector | 120 | 689,439 | 80,694 | 770,133 |
| curator | 120 | 1,214,131 | 46,543 | 1,260,674 |
| grader* | 100 | 406,705 | 69,360 | 476,065 |
| **TOTAL** | **440** | **3,663,460** | **341,690** | **4,005,150** |

### FBL v4
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 1,441,785 | 168,898 | 1,610,683 |
| reflector | 120 | 814,172 | 95,181 | 909,353 |
| curator | 120 | 1,299,667 | 44,200 | 1,343,867 |
| grader* | 100 | 414,338 | 69,360 | 483,698 |
| **TOTAL** | **440** | **3,969,962** | **377,639** | **4,347,601** |

### Token summary
| | baseline | pure+files | FBL v3 | FBL v4 |
|---|---|---|---|---|
| total calls (incl. grader) | 400 | 200 | 440 | 440 |
| total tokens (incl. grader) | 3,537,537 | 681,165 | 4,005,150 | 4,347,601 |

_pure is by far the cheapest (1 gen + 1 grader/task) and the highest-scoring; the FBL variants spend the most (dual-playbook reflect/curate) for a still-lower score._

## 4. All 100 tasks — score line-up (dataset order)

Winner **bolded**. `Δv4−v3` shows the effect of the v4 edit per task.

| # | task_id | sector | baseline | pure+files | FBL v3 | FBL v4 | Δv4−v3 | best |
|---|---|---|---|---|---|---|---|---|
| 1 | 83d10b06 | Professional, Scie | 0.000 | 0.270 | **0.444** | 0.429 | -0.016 | FBL |
| 2 | f84ea6ac | Government | **0.845** | 0.741 | 0.793 | **0.845** | +0.052 | baseline |
| 3 | 99ac6944 | Information | 0.256 | **0.841** | 0.549 | 0.598 | +0.049 | pure |
| 4 | 1b1ade2d | Manufacturing | 0.449 | **0.899** | 0.638 | 0.667 | +0.029 | pure |
| 5 | 575f8679 | Government | 0.631 | **0.908** | 0.738 | 0.892 | +0.154 | pure |
| 6 | 36d567ba | Government | **0.741** | **0.741** | **0.741** | **0.741** | +0.000 | baseline |
| 7 | cebf301e | Professional, Scie | 0.855 | **0.968** | 0.887 | 0.952 | +0.065 | pure |
| 8 | a10ec48c | Real Estate and Re | 0.543 | 0.600 | 0.600 | **0.714** | +0.114 | FBL |
| 9 | a0ef404e | Real Estate and Re | 0.707 | 0.741 | **0.759** | **0.759** | +0.000 | FBL |
| 10 | f3351922 | Finance and Insura | 0.518 | 0.753 | **0.765** | 0.506 | -0.259 | FBL |
| 11 | 401a07f1 | Information | **0.939** | **0.939** | **0.939** | 0.848 | -0.091 | baseline |
| 12 | 8c8fc328 | Information | 0.882 | **0.902** | 0.882 | 0.863 | -0.020 | pure |
| 13 | a1963a68 | Finance and Insura | 0.606 | **0.697** | 0.621 | 0.606 | -0.015 | pure |
| 14 | 8079e27d | Finance and Insura | **0.418** | 0.182 | 0.182 | 0.164 | -0.018 | baseline |
| 15 | ec591973 | Wholesale Trade | 0.429 | **0.571** | 0.558 | 0.545 | -0.013 | pure |
| 16 | 6dcae3f5 | Health Care and So | **0.000** | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 17 | 8c823e32 | Government | 0.770 | **0.865** | 0.833 | 0.810 | -0.024 | pure |
| 18 | bf68f2ad | Manufacturing | 0.018 | 0.554 | **0.732** | 0.429 | -0.304 | FBL |
| 19 | bd72994f | Retail Trade | **0.828** | 0.793 | **0.828** | 0.724 | -0.103 | baseline |
| 20 | 8f9e8bcd | Retail Trade | **0.959** | 0.939 | **0.959** | 0.939 | -0.020 | baseline |
| 21 | 8a7b6fca | Manufacturing | 0.675 | 0.000 | **0.925** | 0.050 | -0.875 | FBL |
| 22 | cd9efc18 | Professional, Scie | 0.650 | **0.821** | 0.814 | 0.657 | -0.157 | pure |
| 23 | 5e2b6aab | Manufacturing | 0.294 | **1.000** | 0.426 | 0.368 | -0.059 | pure |
| 24 | f1be6436 | Health Care and So | 0.365 | **0.770** | 0.365 | 0.541 | +0.176 | pure |
| 25 | 74d6e8b0 | Health Care and So | 0.188 | **0.406** | 0.275 | 0.217 | -0.058 | pure |
| 26 | 60221cd0 | Information | 0.690 | **0.793** | 0.655 | 0.759 | +0.103 | pure |
| 27 | 1a78e076 | Health Care and So | 0.541 | 0.753 | 0.788 | **0.812** | +0.024 | FBL |
| 28 | b5d2e6f1 | Wholesale Trade | 0.000 | 0.492 | 0.046 | **0.738** | +0.692 | FBL |
| 29 | 9a0d8d36 | Finance and Insura | 0.712 | **0.827** | 0.596 | 0.404 | -0.192 | pure |
| 30 | 91060ff0 | Retail Trade | 0.636 | **0.855** | 0.745 | **0.855** | +0.109 | pure |
| 31 | ae0c1093 | Retail Trade | 0.636 | **0.682** | **0.682** | **0.682** | +0.000 | pure |
| 32 | 6241e678 | Information | 0.577 | 0.939 | **0.963** | 0.595 | -0.368 | FBL |
| 33 | 02aa1805 | Professional, Scie | 0.826 | **0.977** | 0.814 | 0.930 | +0.116 | pure |
| 34 | a99d85fc | Real Estate and Re | 0.610 | **0.961** | 0.753 | 0.948 | +0.195 | pure |
| 35 | 46bc7238 | Real Estate and Re | 0.806 | 0.776 | 0.791 | **0.821** | +0.030 | FBL |
| 36 | 5ad0c554 | Real Estate and Re | 0.365 | **0.492** | 0.429 | 0.254 | -0.175 | pure |
| 37 | 403b9234 | Government | **0.796** | **0.796** | **0.796** | **0.796** | +0.000 | baseline |
| 38 | 0ec25916 | Health Care and So | 0.776 | 0.836 | **0.851** | 0.806 | -0.045 | FBL |
| 39 | b3573f20 | Wholesale Trade | 0.730 | **0.784** | 0.730 | 0.676 | -0.054 | pure |
| 40 | ab81b076 | Wholesale Trade | 0.327 | **0.731** | 0.385 | 0.423 | +0.038 | pure |
| 41 | b57efde3 | Wholesale Trade | 0.173 | 0.027 | 0.160 | **0.213** | +0.053 | FBL |
| 42 | 9efbcd35 | Finance and Insura | 0.474 | **0.566** | **0.566** | 0.487 | -0.079 | pure |
| 43 | 5349dd7b | Manufacturing | **0.000** | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 44 | 0e386e32 | Professional, Scie | 0.269 | **0.551** | 0.462 | 0.154 | -0.308 | pure |
| 45 | 7b08cd4d | Professional, Scie | 0.663 | **0.674** | 0.607 | 0.562 | -0.045 | pure |
| 46 | a328feea | Government | 0.917 | 0.917 | 0.750 | **1.000** | +0.250 | FBL |
| 47 | f9a1c16c | Information | **0.747** | 0.722 | 0.684 | 0.671 | -0.013 | baseline |
| 48 | 93b336f3 | Manufacturing | 0.553 | 0.724 | 0.803 | **0.895** | +0.092 | FBL |
| 49 | a74ead3b | Government | **0.635** | 0.435 | 0.306 | 0.306 | +0.000 | baseline |
| 50 | 7bbfcfe9 | Government | **0.283** | 0.208 | 0.151 | 0.151 | +0.000 | baseline |
| 51 | c2e8f271 | Professional, Scie | 0.407 | **0.568** | 0.519 | 0.519 | +0.000 | pure |
| 52 | fccaa4a1 | Real Estate and Re | 0.611 | 0.704 | 0.611 | **0.722** | +0.111 | FBL |
| 53 | b7a5912e | Real Estate and Re | 0.325 | **0.579** | 0.439 | 0.447 | +0.009 | pure |
| 54 | 61717508 | Finance and Insura | 0.734 | **0.891** | 0.766 | **0.891** | +0.125 | pure |
| 55 | afe56d05 | Information | 0.556 | **0.632** | **0.632** | 0.590 | -0.043 | pure |
| 56 | e222075d | Information | 0.000 | 0.000 | 0.000 | **0.738** | +0.738 | FBL |
| 57 | 5f6c57dd | Finance and Insura | **0.000** | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 58 | e21cd746 | Finance and Insura | 0.590 | 0.821 | 0.667 | **0.846** | +0.179 | FBL |
| 59 | 62f04c2f | Wholesale Trade | 0.857 | **0.934** | 0.846 | 0.868 | +0.022 | pure |
| 60 | 1aecc095 | Health Care and So | 0.689 | 0.767 | 0.467 | **0.789** | +0.322 | FBL |
| 61 | eb54f575 | Government | 0.651 | 0.778 | 0.746 | **0.841** | +0.095 | FBL |
| 62 | efca245f | Manufacturing | 0.118 | 0.245 | **0.294** | 0.275 | -0.020 | FBL |
| 63 | 211d0093 | Retail Trade | 0.882 | 0.549 | **0.922** | 0.549 | -0.373 | FBL |
| 64 | 0fad6023 | Retail Trade | 0.545 | 0.727 | 0.576 | **0.803** | +0.227 | FBL |
| 65 | 40a99a31 | Manufacturing | 0.423 | **0.701** | **0.701** | 0.598 | -0.103 | pure |
| 66 | a97369c7 | Professional, Scie | 0.603 | **0.865** | 0.643 | 0.651 | +0.008 | pure |
| 67 | 46fc494e | Manufacturing | 0.650 | 0.427 | **0.812** | 0.462 | -0.350 | FBL |
| 68 | 41f6ef59 | Health Care and So | 0.848 | **0.879** | 0.848 | 0.773 | -0.076 | pure |
| 69 | 81db15ff | Health Care and So | 0.710 | 0.710 | **0.783** | 0.696 | -0.087 | FBL |
| 70 | ef8719da | Information | **1.000** | **1.000** | **1.000** | **1.000** | +0.000 | baseline |
| 71 | 1b9ec237 | Health Care and So | 0.746 | **0.881** | 0.797 | **0.881** | +0.085 | pure |
| 72 | f841ddcf | Wholesale Trade | 0.341 | 0.682 | 0.466 | **0.773** | +0.307 | FBL |
| 73 | 664a42e5 | Finance and Insura | 0.760 | **0.940** | 0.820 | **0.940** | +0.120 | pure |
| 74 | 8384083a | Retail Trade | 0.524 | 0.762 | **0.810** | 0.746 | -0.063 | FBL |
| 75 | f9f82549 | Retail Trade | 0.636 | **0.697** | **0.697** | 0.485 | -0.212 | pure |
| 76 | e14e32ba | Information | 0.552 | **0.586** | 0.448 | 0.517 | +0.069 | pure |
| 77 | fd6129bd | Professional, Scie | 0.721 | **0.860** | 0.756 | 0.837 | +0.081 | pure |
| 78 | 55ddb773 | Real Estate and Re | 0.304 | 0.321 | 0.321 | **0.429** | +0.107 | FBL |
| 79 | 2d06bc0a | Real Estate and Re | 0.758 | **0.879** | 0.803 | 0.833 | +0.030 | pure |
| 80 | 11593a50 | Real Estate and Re | **0.283** | **0.283** | 0.151 | 0.151 | +0.000 | baseline |
| 81 | 1bff4551 | Government | 0.558 | **0.721** | 0.651 | 0.698 | +0.047 | pure |
| 82 | 116e791e | Health Care and So | 0.562 | 0.812 | 0.828 | **0.859** | +0.031 | FBL |
| 83 | a69be28f | Wholesale Trade | 0.198 | 0.178 | **0.208** | **0.208** | +0.000 | FBL |
| 84 | d7cfae6f | Wholesale Trade | 0.198 | 0.619 | **0.825** | 0.246 | -0.579 | FBL |
| 85 | 15d37511 | Wholesale Trade | 0.092 | 0.248 | 0.183 | **0.303** | +0.119 | FBL |
| 86 | 1d4672c8 | Finance and Insura | 0.542 | 0.814 | 0.814 | **0.932** | +0.119 | FBL |
| 87 | a4a9195c | Manufacturing | 0.387 | **0.597** | 0.419 | 0.468 | +0.048 | pure |
| 88 | 7de33b48 | Professional, Scie | 0.683 | **0.833** | 0.817 | **0.833** | +0.017 | pure |
| 89 | 7d7fc9a7 | Professional, Scie | 0.484 | 0.484 | **0.716** | 0.621 | -0.095 | FBL |
| 90 | 27e8912c | Government | **0.736** | 0.717 | 0.623 | 0.623 | +0.000 | baseline |
| 91 | 38889c3b | Information | 0.161 | 0.065 | **0.323** | 0.258 | -0.065 | FBL |
| 92 | 15ddd28d | Manufacturing | 0.649 | **0.737** | 0.667 | 0.702 | +0.035 | pure |
| 93 | bbe0a93b | Government | 0.790 | 0.457 | 0.926 | **0.963** | +0.037 | FBL |
| 94 | 2696757c | Government | 0.439 | **0.463** | 0.244 | 0.244 | +0.000 | pure |
| 95 | 2ea2e5b5 | Professional, Scie | 0.000 | **0.353** | 0.235 | 0.259 | +0.024 | pure |
| 96 | f5d428fd | Real Estate and Re | 0.774 | 0.811 | 0.830 | **0.887** | +0.057 | FBL |
| 97 | aa071045 | Real Estate and Re | 0.686 | 0.674 | **0.767** | 0.744 | -0.023 | FBL |
| 98 | 0ed38524 | Finance and Insura | 0.596 | 0.635 | **0.769** | 0.654 | -0.115 | FBL |
| 99 | 9a8c8e28 | Information | 0.397 | 0.411 | 0.344 | **0.510** | +0.166 | FBL |
| 100 | c94452e4 | Information | 0.000 | **0.875** | 0.000 | 0.536 | +0.536 | pure |

_(tied-)best count: pure 53, v4 36, v3 32, baseline 16 out of 100._
