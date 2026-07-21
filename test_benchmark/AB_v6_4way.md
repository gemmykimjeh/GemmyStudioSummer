# GDPval 4-way — baseline ACE vs pure LLM+tools vs FBL v5 vs FBL v6

Same 100 gdpval train tasks, same attachment file-I/O + codegen pipeline, same Gemini generator + robust grader (rotation proxy, temperature-0 + retry). Both FBL runs are cold starts from the packaged seeds. Only the scaffold around the generator changes.

- **baseline** `ace_gdpval` — single-playbook ACE, gen→reflect→curator. **Untouched** (control).
- **pure+tools** — one raw LLM call + the same file/codegen pipeline, no ACE scaffold.
- **FBL v5** `ace_dual_gdpval` — ACE orchestrator + immutable rulebook, ①source-file manifest, ②read-source-not-inline rule, execute-and-repair loop (traceback+schema → fix, ≤2×), independent-path self-verify (rb-chk-01).
- **FBL v6** — v5 **+ playbook growth gate**: a task scoring above 0.75 still reflects (helpful/harmful tagging, grader-aligned counting, net-harmful prune) but may **not grow** the playbook — the curator ADD is skipped. Learn from what went wrong, not from what went right.

## 1. Overall scores

| metric | baseline | pure+tools | FBL v5 | FBL v6 |
|---|---|---|---|---|
| **mean score** | 0.5159 | 0.5880 | 0.6037 | **0.6288** 🥇 |
| pass ≥0.5 | 60 | 67 | **71** 🥇 | **71** 🥇 |
| good ≥0.8 | 14 | 29 | 30 | 30 |
| perfect 1.0 | 0 | 7 | 2 | 2 |
| zeros | 8 | 14 | 4 | 4 |
| avg deliverable chars | 2170 | 3625 | 3688 | 3656 |
| final playbook chars | n/a | — | 59,948 | **42,285** |

**v6 vs v5 head-to-head: 46–28** (tie 26). v6 reaches a higher mean with a **29% smaller playbook** and 38 skipped curator calls — the pass/good/zero counts are identical, so the gain is a broad lift across mid-range tasks rather than a few rescued failures.

Other head-to-heads: v6 vs baseline 78–18, v6 vs pure 44–38, v5 vs pure 42–46.

## 2. By sector (mean score)

| sector | n | baseline | pure+tools | FBL v5 | FBL v6 |
|---|---|---|---|---|---|
| Retail Trade | 8 | 0.695 | 0.821 | 0.819 | **0.826** |
| Information | 13 | 0.523 | 0.713 | **0.748** | 0.728 |
| Government | 13 | 0.614 | 0.654 | **0.709** | 0.705 |
| Finance and Insurance | 11 | 0.502 | 0.506 | 0.638 | **0.702** |
| Health Care and Social Assistance | 10 | 0.545 | 0.594 | 0.629 | **0.630** |
| Professional, Scientific, and Technical Services | 12 | 0.462 | 0.536 | 0.569 | **0.625** |
| Real Estate and Rental and Leasing | 12 | **0.588** | 0.466 | 0.493 | 0.587 |
| Manufacturing | 11 | 0.369 | **0.568** | 0.516 | 0.525 |
| Wholesale Trade | 10 | 0.363 | **0.468** | 0.314 | 0.330 |

## 3. Token usage by role (calls + tokens)

Brain roles (generator/reflector/curator) = **exact** (`detailed_llm_logs`, windowed per run by completion time). `grader*` and pure's `llm-call*` = **reconstructed** (lengths → chars/4, Gemini-approx; not logged).

### baseline
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 1,304,139 | 104,661 | 1,408,800 |
| reflector | 100 | 458,404 | 53,111 | 511,515 |
| curator | 100 | 1,304,757 | 51,058 | 1,355,815 |
| grader* | 100 | 373,796 | 69,360 | 443,156 |
| **TOTAL** | **400** | **3,441,096** | **278,190** | **3,719,286** |

### pure+tools
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| llm-call* | 100 | 100,134 | 90,591 | 190,725 |
| grader* | 100 | 410,164 | 69,360 | 479,524 |
| **TOTAL** | **200** | **510,298** | **159,951** | **670,249** |

### FBL v5
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 1,197,970 | 116,834 | 1,314,804 |
| reflector | 100 | 740,412 | 44,345 | 784,757 |
| curator | 100 | 1,133,288 | 29,096 | 1,162,384 |
| grader* | 100 | 411,745 | 69,360 | 481,105 |
| **TOTAL** | **400** | **3,483,415** | **259,635** | **3,743,050** |

### FBL v6
| role | calls | prompt tok | resp tok | total |
|---|---|---|---|---|
| generator | 100 | 959,569 | 114,962 | 1,074,531 |
| reflector | 100 | 739,613 | 44,760 | 784,373 |
| curator | 62 | 552,901 | 18,493 | 571,394 |
| grader* | 100 | 410,951 | 69,360 | 480,311 |
| **TOTAL** | **362** | **2,663,034** | **247,575** | **2,910,609** |

### Token summary
| | baseline | pure+tools | FBL v5 | FBL v6 |
|---|---|---|---|---|
| total calls | 400 | 200 | 400 | 362 |
| total tokens | 3,719,286 | 670,249 | 3,743,050 | 2,910,609 |

_v6 skips the curator on 38 already-good tasks; the reflector still runs on every task so helpful/harmful evidence keeps accruing._

## 4. All 100 tasks — score line-up (dataset order)

Winner **bolded**. `Δv6−v5` = effect of the growth gate per task.

| # | task_id | sector | gold | baseline | pure | v5 | v6 | Δv6−v5 | best |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 83d10b06 | Professional,  | xlsx | 0.000 | 0.000 | 0.460 | **0.730** | +0.270 | v6 |
| 2 | f84ea6ac | Government | docx | 0.707 | **0.966** | 0.879 | 0.845 | -0.034 | pure |
| 3 | 99ac6944 | Information | pdf | 0.268 | **0.744** | 0.732 | **0.744** | +0.012 | pure |
| 4 | 1b1ade2d | Manufacturing | docx | 0.435 | 0.594 | 0.580 | **0.638** | +0.058 | v6 |
| 5 | 575f8679 | Government | - | 0.585 | 0.754 | 0.877 | **0.908** | +0.031 | v6 |
| 6 | 36d567ba | Government | docx | 0.704 | **0.778** | 0.741 | 0.741 | +0.000 | pure |
| 7 | cebf301e | Professional,  | docx | 0.887 | 0.887 | **0.919** | **0.919** | +0.000 | v5 |
| 8 | a10ec48c | Real Estate an | docx | 0.543 | 0.429 | 0.514 | **0.657** | +0.143 | v6 |
| 9 | a0ef404e | Real Estate an | docx | 0.724 | 0.793 | **0.810** | 0.759 | -0.052 | v5 |
| 10 | f3351922 | Finance and In | - | 0.624 | **0.741** | 0.694 | 0.706 | +0.012 | pure |
| 11 | 401a07f1 | Information | - | 0.939 | **1.000** | 0.697 | 0.909 | +0.212 | pure |
| 12 | 8c8fc328 | Information | docx | 0.824 | **1.000** | 0.980 | 0.980 | +0.000 | pure |
| 13 | a1963a68 | Finance and In | - | **0.667** | 0.652 | 0.636 | 0.652 | +0.015 | baseline |
| 14 | 8079e27d | Finance and In | - | 0.182 | 0.473 | 0.618 | **0.636** | +0.018 | v6 |
| 15 | ec591973 | Wholesale Trad | pptx | 0.429 | 0.468 | **0.506** | 0.481 | -0.026 | v5 |
| 16 | 6dcae3f5 | Health Care an | docx,xlsx | 0.000 | 0.000 | **0.030** | **0.030** | +0.000 | v5 |
| 17 | 8c823e32 | Government | - | **0.825** | 0.802 | **0.825** | 0.817 | -0.008 | baseline |
| 18 | bf68f2ad | Manufacturing | xlsx | 0.357 | **0.393** | 0.339 | **0.393** | +0.054 | pure |
| 19 | bd72994f | Retail Trade | - | 0.793 | 0.724 | **0.862** | 0.793 | -0.069 | v5 |
| 20 | 8f9e8bcd | Retail Trade | docx | 0.959 | **1.000** | 0.980 | **1.000** | +0.020 | pure |
| 21 | 8a7b6fca | Manufacturing | pdf | 0.500 | **0.825** | 0.600 | 0.425 | -0.175 | pure |
| 22 | cd9efc18 | Professional,  | - | 0.686 | 0.686 | 0.743 | **0.750** | +0.007 | v6 |
| 23 | 5e2b6aab | Manufacturing | pdf,zip | 0.309 | 0.691 | 0.632 | **0.706** | +0.074 | v6 |
| 24 | f1be6436 | Health Care an | docx | 0.351 | **0.892** | 0.365 | 0.378 | +0.014 | pure |
| 25 | 74d6e8b0 | Health Care an | docx | 0.203 | **0.304** | 0.246 | **0.304** | +0.058 | pure |
| 26 | 60221cd0 | Information | pdf | **0.724** | 0.655 | 0.655 | 0.655 | +0.000 | baseline |
| 27 | 1a78e076 | Health Care an | docx | 0.600 | 0.765 | 0.741 | **0.906** | +0.165 | v6 |
| 28 | b5d2e6f1 | Wholesale Trad | xlsx | 0.000 | **0.615** | 0.323 | 0.338 | +0.015 | pure |
| 29 | 9a0d8d36 | Finance and In | pptx | 0.462 | **0.865** | 0.808 | 0.692 | -0.115 | pure |
| 30 | 91060ff0 | Retail Trade | - | 0.600 | 0.891 | **0.945** | 0.909 | -0.036 | v5 |
| 31 | ae0c1093 | Retail Trade | pdf | 0.682 | **0.818** | 0.727 | **0.818** | +0.091 | pure |
| 32 | 6241e678 | Information | pdf | 0.730 | 0.607 | **0.896** | 0.620 | -0.276 | v5 |
| 33 | 02aa1805 | Professional,  | xlsx | **0.709** | 0.674 | 0.651 | 0.651 | +0.000 | baseline |
| 34 | a99d85fc | Real Estate an | xlsx | 0.610 | 0.000 | 0.545 | **0.961** | +0.416 | v6 |
| 35 | 46bc7238 | Real Estate an | pdf | 0.836 | 0.836 | 0.836 | **0.866** | +0.030 | v6 |
| 36 | 5ad0c554 | Real Estate an | docx | 0.270 | 0.254 | **0.302** | 0.143 | -0.159 | v5 |
| 37 | 403b9234 | Government | pptx | 0.759 | **0.981** | **0.981** | **0.981** | +0.000 | pure |
| 38 | 0ec25916 | Health Care an | pdf | 0.821 | **0.866** | 0.836 | 0.836 | +0.000 | pure |
| 39 | b3573f20 | Wholesale Trad | pdf | 0.486 | **0.838** | 0.595 | 0.784 | +0.189 | pure |
| 40 | ab81b076 | Wholesale Trad | pdf | 0.558 | 0.500 | 0.500 | **0.596** | +0.096 | v6 |
| 41 | b57efde3 | Wholesale Trad | xlsx | 0.200 | 0.200 | **0.213** | **0.213** | +0.000 | v5 |
| 42 | 9efbcd35 | Finance and In | - | 0.553 | 0.645 | 0.618 | **0.658** | +0.039 | v6 |
| 43 | 5349dd7b | Manufacturing | xlsx | **0.000** | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 44 | 0e386e32 | Professional,  | zip | 0.308 | 0.577 | **0.590** | 0.538 | -0.051 | v5 |
| 45 | 7b08cd4d | Professional,  | xlsx | 0.539 | **0.596** | 0.000 | 0.000 | +0.000 | pure |
| 46 | a328feea | Government | docx | 0.833 | **1.000** | 0.917 | 0.917 | +0.000 | pure |
| 47 | f9a1c16c | Information | pdf | 0.658 | 0.886 | 0.709 | **0.987** | +0.278 | v6 |
| 48 | 93b336f3 | Manufacturing | docx | 0.566 | **0.750** | 0.697 | 0.737 | +0.039 | pure |
| 49 | a74ead3b | Government | pptx | 0.282 | **0.435** | 0.412 | 0.412 | +0.000 | pure |
| 50 | 7bbfcfe9 | Government | xlsx | 0.151 | **0.189** | **0.189** | **0.189** | +0.000 | pure |
| 51 | c2e8f271 | Professional,  | docx | 0.407 | 0.469 | 0.457 | **0.531** | +0.074 | v6 |
| 52 | fccaa4a1 | Real Estate an | pdf | 0.704 | 0.593 | **0.741** | 0.685 | -0.056 | v5 |
| 53 | b7a5912e | Real Estate an | xlsx | 0.518 | 0.000 | 0.053 | **0.605** | +0.553 | v6 |
| 54 | 61717508 | Finance and In | pdf | 0.828 | 0.000 | 0.578 | **0.891** | +0.312 | v6 |
| 55 | afe56d05 | Information | - | 0.564 | 0.581 | **0.590** | 0.521 | -0.068 | v5 |
| 56 | e222075d | Information | mp4 | 0.148 | 0.000 | 0.738 | **0.770** | +0.033 | v6 |
| 57 | 5f6c57dd | Finance and In | - | **0.000** | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 58 | e21cd746 | Finance and In | pdf | 0.487 | 0.487 | 0.846 | **0.872** | +0.026 | v6 |
| 59 | 62f04c2f | Wholesale Trad | docx,xlsx | 0.824 | **0.901** | 0.396 | 0.396 | +0.000 | pure |
| 60 | 1aecc095 | Health Care an | docx | 0.578 | 0.633 | **0.756** | 0.600 | -0.156 | v5 |
| 61 | eb54f575 | Government | pdf | 0.714 | 0.778 | 0.667 | **0.841** | +0.175 | v6 |
| 62 | efca245f | Manufacturing | xlsx | 0.275 | 0.461 | 0.020 | **0.539** | +0.520 | v6 |
| 63 | 211d0093 | Retail Trade | pdf | 0.843 | **1.000** | 0.961 | 0.980 | +0.020 | pure |
| 64 | 0fad6023 | Retail Trade | xlsx | 0.470 | **0.697** | **0.697** | **0.697** | +0.000 | pure |
| 65 | 40a99a31 | Manufacturing | pdf,xlsx | 0.619 | **0.845** | 0.691 | 0.753 | +0.062 | pure |
| 66 | a97369c7 | Professional,  | docx | 0.619 | 0.675 | **0.706** | 0.667 | -0.040 | v5 |
| 67 | 46fc494e | Manufacturing | pdf,png,py | 0.000 | 0.607 | **1.000** | 0.462 | -0.538 | v5 |
| 68 | 41f6ef59 | Health Care an | docx,xlsx | 0.818 | 0.000 | **0.864** | 0.833 | -0.030 | v5 |
| 69 | 81db15ff | Health Care an | xlsx | 0.710 | **0.783** | **0.783** | **0.783** | +0.000 | pure |
| 70 | ef8719da | Information | - | 0.981 | **1.000** | **1.000** | **1.000** | +0.000 | pure |
| 71 | 1b9ec237 | Health Care an | pptx | 0.712 | **0.881** | 0.831 | 0.864 | +0.034 | pure |
| 72 | f841ddcf | Wholesale Trad | xlsx | 0.352 | **0.716** | 0.375 | 0.023 | -0.352 | pure |
| 73 | 664a42e5 | Finance and In | pptx | 0.560 | **0.880** | 0.840 | 0.820 | -0.020 | pure |
| 74 | 8384083a | Retail Trade | pdf | 0.603 | 0.714 | **0.746** | 0.714 | -0.032 | v5 |
| 75 | f9f82549 | Retail Trade | pdf | 0.606 | **0.727** | 0.636 | 0.697 | +0.061 | pure |
| 76 | e14e32ba | Information | - | 0.586 | **0.655** | **0.655** | **0.655** | +0.000 | pure |
| 77 | fd6129bd | Professional,  | docx,pdf | 0.698 | 0.651 | 0.802 | **0.814** | +0.012 | v6 |
| 78 | 55ddb773 | Real Estate an | - | 0.286 | 0.339 | 0.339 | **0.429** | +0.089 | v6 |
| 79 | 2d06bc0a | Real Estate an | docx | 0.803 | 0.818 | 0.833 | **0.864** | +0.030 | v6 |
| 80 | 11593a50 | Real Estate an | pdf | **0.283** | 0.000 | 0.075 | 0.075 | +0.000 | baseline |
| 81 | 1bff4551 | Government | pdf | 0.558 | 0.465 | **0.581** | 0.395 | -0.186 | v5 |
| 82 | 116e791e | Health Care an | pdf | 0.656 | 0.812 | **0.844** | 0.766 | -0.078 | v5 |
| 83 | a69be28f | Wholesale Trad | pdf | 0.208 | 0.198 | 0.000 | **0.228** | +0.228 | v6 |
| 84 | d7cfae6f | Wholesale Trad | xlsx | **0.476** | 0.000 | 0.016 | 0.000 | -0.016 | baseline |
| 85 | 15d37511 | Wholesale Trad | xlsx | 0.101 | **0.248** | 0.220 | 0.239 | +0.018 | pure |
| 86 | 1d4672c8 | Finance and In | pdf,xlsx | 0.492 | 0.000 | 0.508 | **0.932** | +0.424 | v6 |
| 87 | a4a9195c | Manufacturing | docx | 0.419 | 0.500 | 0.484 | **0.548** | +0.065 | v6 |
| 88 | 7de33b48 | Professional,  | zip | 0.617 | 0.900 | **0.967** | **0.967** | +0.000 | v5 |
| 89 | 7d7fc9a7 | Professional,  | xlsx | 0.074 | 0.000 | 0.347 | **0.474** | +0.126 | v6 |
| 90 | 27e8912c | Government | docx,pdf | 0.717 | 0.000 | **0.811** | 0.774 | -0.038 | v5 |
| 91 | 38889c3b | Information | zip | 0.032 | **1.000** | 0.871 | 0.790 | -0.081 | pure |
| 92 | 15ddd28d | Manufacturing | docx | 0.579 | 0.579 | **0.632** | 0.579 | -0.053 | v5 |
| 93 | bbe0a93b | Government | pdf | 0.778 | 0.864 | **0.877** | **0.877** | +0.000 | v5 |
| 94 | 2696757c | Government | pdf | 0.366 | **0.488** | 0.463 | 0.463 | +0.000 | pure |
| 95 | 2ea2e5b5 | Professional,  | pptx | 0.000 | 0.318 | 0.188 | **0.459** | +0.271 | v6 |
| 96 | f5d428fd | Real Estate an | pdf | **0.792** | 0.736 | 0.585 | 0.717 | +0.132 | baseline |
| 97 | aa071045 | Real Estate an | docx,xlsx | 0.686 | **0.791** | 0.279 | 0.279 | +0.000 | pure |
| 98 | 0ed38524 | Finance and In | pdf | 0.673 | 0.827 | **0.865** | **0.865** | +0.000 | v5 |
| 99 | 9a8c8e28 | Information | pdf | 0.338 | 0.377 | **0.470** | 0.444 | -0.026 | v5 |
| 100 | c94452e4 | Information | - | 0.000 | **0.768** | 0.732 | 0.393 | -0.339 | pure |

_(tied-)best count: pure 40, FBL v6 47, FBL v5 34, baseline 9 / 100._

## 5. Caveat — same 100 tasks

Every arm here is measured on the **same first 100 tasks**, and FBL v2→v6 were iterated against those same tasks. The v6 gain is therefore **not yet shown to generalize**; a held-out slice (tasks 101-200) is required to separate real improvement from fitting this particular 100. Gold reference files were used only to validate the measuring apparatus (the achievable ceiling), never to shape agent behaviour.
