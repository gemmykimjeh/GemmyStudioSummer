# GDPval 3-way — baseline ACE vs pure LLM+tools vs FBL v5

Same 100 gdpval train tasks, same attachment file-I/O + codegen pipeline, same Gemini generator + robust grader (rotation proxy, temperature-0 + retry). Only the scaffold around the generator changes.

- **baseline** `ace_gdpval` — single-playbook ACE, gen→reflect→curator. **Untouched** (control).
- **pure+tools** — one raw LLM call + the same file/codegen pipeline, no ACE scaffold. (the ceiling reference)
- **FBL v5** `ace_dual_gdpval` — single-playbook ACE + immutable rulebook, plus this cycle's changes: ①source-file manifest, ②read-source-not-inline rule (rb-fmt-08), execute-and-repair loop (run code → feed traceback+schema → fix, ≤2×), independent-path self-verify (rb-chk-01).

## 1. Overall scores

| metric | baseline | pure+tools | FBL v5 |
|---|---|---|---|
| **mean score** | 0.5159 | 0.5880 | **0.6037** 🥇 |
| pass ≥0.5 | 60 | 67 | **71** 🥇 |
| good ≥0.8 | 14 | 29 | 30 |
| perfect 1.0 | 0 | 7 | 2 |
| zeros | 8 | 14 | 4 |
| avg deliverable chars | 2170 | 3625 | 3688 |

Head-to-head: FBL v5 vs baseline 77–19 (tie 4), FBL v5 vs pure 42–46 (tie 12).

## 2. By sector (mean score)

| sector | n | baseline | pure+tools | FBL v5 |
|---|---|---|---|---|
| Retail Trade | 8 | 0.695 | **0.821** | 0.819 |
| Information | 13 | 0.523 | 0.713 | **0.748** |
| Government | 13 | 0.614 | 0.654 | **0.709** |
| Finance and Insurance | 11 | 0.502 | 0.506 | **0.638** |
| Health Care and Social Assistance | 10 | 0.545 | 0.594 | **0.629** |
| Professional, Scientific, and Technical Services | 12 | 0.462 | 0.536 | **0.569** |
| Manufacturing | 11 | 0.369 | **0.568** | 0.516 |
| Real Estate and Rental and Leasing | 12 | **0.588** | 0.466 | 0.493 |
| Wholesale Trade | 10 | 0.363 | **0.468** | 0.314 |

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

### Token summary
| | baseline | pure+tools | FBL v5 |
|---|---|---|---|
| total calls | 400 | 200 | 400 |
| total tokens | 3,719,286 | 670,249 | 3,743,050 |

## 4. All 100 tasks — score line-up (dataset order)

Winner **bolded**. `Δv5−base` = FBL v5's effect over the untouched baseline per task.

| # | task_id | sector | gold | baseline | pure | FBL v5 | Δv5−base | best |
|---|---|---|---|---|---|---|---|---|
| 1 | 83d10b06 | Professional,  | xlsx | 0.000 | 0.000 | **0.460** | +0.460 | v5 |
| 2 | f84ea6ac | Government | docx | 0.707 | **0.966** | 0.879 | +0.172 | pure |
| 3 | 99ac6944 | Information | pdf | 0.268 | **0.744** | 0.732 | +0.463 | pure |
| 4 | 1b1ade2d | Manufacturing | docx | 0.435 | **0.594** | 0.580 | +0.145 | pure |
| 5 | 575f8679 | Government | - | 0.585 | 0.754 | **0.877** | +0.292 | v5 |
| 6 | 36d567ba | Government | docx | 0.704 | **0.778** | 0.741 | +0.037 | pure |
| 7 | cebf301e | Professional,  | docx | 0.887 | 0.887 | **0.919** | +0.032 | v5 |
| 8 | a10ec48c | Real Estate an | docx | **0.543** | 0.429 | 0.514 | -0.029 | baseline |
| 9 | a0ef404e | Real Estate an | docx | 0.724 | 0.793 | **0.810** | +0.086 | v5 |
| 10 | f3351922 | Finance and In | - | 0.624 | **0.741** | 0.694 | +0.071 | pure |
| 11 | 401a07f1 | Information | - | 0.939 | **1.000** | 0.697 | -0.242 | pure |
| 12 | 8c8fc328 | Information | docx | 0.824 | **1.000** | 0.980 | +0.157 | pure |
| 13 | a1963a68 | Finance and In | - | **0.667** | 0.652 | 0.636 | -0.030 | baseline |
| 14 | 8079e27d | Finance and In | - | 0.182 | 0.473 | **0.618** | +0.436 | v5 |
| 15 | ec591973 | Wholesale Trad | pptx | 0.429 | 0.468 | **0.506** | +0.078 | v5 |
| 16 | 6dcae3f5 | Health Care an | docx,xlsx | 0.000 | 0.000 | **0.030** | +0.030 | v5 |
| 17 | 8c823e32 | Government | - | **0.825** | 0.802 | **0.825** | +0.000 | baseline |
| 18 | bf68f2ad | Manufacturing | xlsx | 0.357 | **0.393** | 0.339 | -0.018 | pure |
| 19 | bd72994f | Retail Trade | - | 0.793 | 0.724 | **0.862** | +0.069 | v5 |
| 20 | 8f9e8bcd | Retail Trade | docx | 0.959 | **1.000** | 0.980 | +0.020 | pure |
| 21 | 8a7b6fca | Manufacturing | pdf | 0.500 | **0.825** | 0.600 | +0.100 | pure |
| 22 | cd9efc18 | Professional,  | - | 0.686 | 0.686 | **0.743** | +0.057 | v5 |
| 23 | 5e2b6aab | Manufacturing | pdf,zip | 0.309 | **0.691** | 0.632 | +0.324 | pure |
| 24 | f1be6436 | Health Care an | docx | 0.351 | **0.892** | 0.365 | +0.014 | pure |
| 25 | 74d6e8b0 | Health Care an | docx | 0.203 | **0.304** | 0.246 | +0.043 | pure |
| 26 | 60221cd0 | Information | pdf | **0.724** | 0.655 | 0.655 | -0.069 | baseline |
| 27 | 1a78e076 | Health Care an | docx | 0.600 | **0.765** | 0.741 | +0.141 | pure |
| 28 | b5d2e6f1 | Wholesale Trad | xlsx | 0.000 | **0.615** | 0.323 | +0.323 | pure |
| 29 | 9a0d8d36 | Finance and In | pptx | 0.462 | **0.865** | 0.808 | +0.346 | pure |
| 30 | 91060ff0 | Retail Trade | - | 0.600 | 0.891 | **0.945** | +0.345 | v5 |
| 31 | ae0c1093 | Retail Trade | pdf | 0.682 | **0.818** | 0.727 | +0.045 | pure |
| 32 | 6241e678 | Information | pdf | 0.730 | 0.607 | **0.896** | +0.166 | v5 |
| 33 | 02aa1805 | Professional,  | xlsx | **0.709** | 0.674 | 0.651 | -0.058 | baseline |
| 34 | a99d85fc | Real Estate an | xlsx | **0.610** | 0.000 | 0.545 | -0.065 | baseline |
| 35 | 46bc7238 | Real Estate an | pdf | **0.836** | **0.836** | **0.836** | +0.000 | baseline |
| 36 | 5ad0c554 | Real Estate an | docx | 0.270 | 0.254 | **0.302** | +0.032 | v5 |
| 37 | 403b9234 | Government | pptx | 0.759 | **0.981** | **0.981** | +0.222 | pure |
| 38 | 0ec25916 | Health Care an | pdf | 0.821 | **0.866** | 0.836 | +0.015 | pure |
| 39 | b3573f20 | Wholesale Trad | pdf | 0.486 | **0.838** | 0.595 | +0.108 | pure |
| 40 | ab81b076 | Wholesale Trad | pdf | **0.558** | 0.500 | 0.500 | -0.058 | baseline |
| 41 | b57efde3 | Wholesale Trad | xlsx | 0.200 | 0.200 | **0.213** | +0.013 | v5 |
| 42 | 9efbcd35 | Finance and In | - | 0.553 | **0.645** | 0.618 | +0.066 | pure |
| 43 | 5349dd7b | Manufacturing | xlsx | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 44 | 0e386e32 | Professional,  | zip | 0.308 | 0.577 | **0.590** | +0.282 | v5 |
| 45 | 7b08cd4d | Professional,  | xlsx | 0.539 | **0.596** | 0.000 | -0.539 | pure |
| 46 | a328feea | Government | docx | 0.833 | **1.000** | 0.917 | +0.083 | pure |
| 47 | f9a1c16c | Information | pdf | 0.658 | **0.886** | 0.709 | +0.051 | pure |
| 48 | 93b336f3 | Manufacturing | docx | 0.566 | **0.750** | 0.697 | +0.132 | pure |
| 49 | a74ead3b | Government | pptx | 0.282 | **0.435** | 0.412 | +0.129 | pure |
| 50 | 7bbfcfe9 | Government | xlsx | 0.151 | **0.189** | **0.189** | +0.038 | pure |
| 51 | c2e8f271 | Professional,  | docx | 0.407 | **0.469** | 0.457 | +0.049 | pure |
| 52 | fccaa4a1 | Real Estate an | pdf | 0.704 | 0.593 | **0.741** | +0.037 | v5 |
| 53 | b7a5912e | Real Estate an | xlsx | **0.518** | 0.000 | 0.053 | -0.465 | baseline |
| 54 | 61717508 | Finance and In | pdf | **0.828** | 0.000 | 0.578 | -0.250 | baseline |
| 55 | afe56d05 | Information | - | 0.564 | 0.581 | **0.590** | +0.026 | v5 |
| 56 | e222075d | Information | mp4 | 0.148 | 0.000 | **0.738** | +0.590 | v5 |
| 57 | 5f6c57dd | Finance and In | - | **0.000** | **0.000** | **0.000** | +0.000 | baseline |
| 58 | e21cd746 | Finance and In | pdf | 0.487 | 0.487 | **0.846** | +0.359 | v5 |
| 59 | 62f04c2f | Wholesale Trad | docx,xlsx | 0.824 | **0.901** | 0.396 | -0.429 | pure |
| 60 | 1aecc095 | Health Care an | docx | 0.578 | 0.633 | **0.756** | +0.178 | v5 |
| 61 | eb54f575 | Government | pdf | 0.714 | **0.778** | 0.667 | -0.048 | pure |
| 62 | efca245f | Manufacturing | xlsx | 0.275 | **0.461** | 0.020 | -0.255 | pure |
| 63 | 211d0093 | Retail Trade | pdf | 0.843 | **1.000** | 0.961 | +0.118 | pure |
| 64 | 0fad6023 | Retail Trade | xlsx | 0.470 | **0.697** | **0.697** | +0.227 | pure |
| 65 | 40a99a31 | Manufacturing | pdf,xlsx | 0.619 | **0.845** | 0.691 | +0.072 | pure |
| 66 | a97369c7 | Professional,  | docx | 0.619 | 0.675 | **0.706** | +0.087 | v5 |
| 67 | 46fc494e | Manufacturing | pdf,png,py | 0.000 | 0.607 | **1.000** | +1.000 | v5 |
| 68 | 41f6ef59 | Health Care an | docx,xlsx | 0.818 | 0.000 | **0.864** | +0.045 | v5 |
| 69 | 81db15ff | Health Care an | xlsx | 0.710 | **0.783** | **0.783** | +0.072 | pure |
| 70 | ef8719da | Information | - | 0.981 | **1.000** | **1.000** | +0.019 | pure |
| 71 | 1b9ec237 | Health Care an | pptx | 0.712 | **0.881** | 0.831 | +0.119 | pure |
| 72 | f841ddcf | Wholesale Trad | xlsx | 0.352 | **0.716** | 0.375 | +0.023 | pure |
| 73 | 664a42e5 | Finance and In | pptx | 0.560 | **0.880** | 0.840 | +0.280 | pure |
| 74 | 8384083a | Retail Trade | pdf | 0.603 | 0.714 | **0.746** | +0.143 | v5 |
| 75 | f9f82549 | Retail Trade | pdf | 0.606 | **0.727** | 0.636 | +0.030 | pure |
| 76 | e14e32ba | Information | - | 0.586 | **0.655** | **0.655** | +0.069 | pure |
| 77 | fd6129bd | Professional,  | docx,pdf | 0.698 | 0.651 | **0.802** | +0.105 | v5 |
| 78 | 55ddb773 | Real Estate an | - | 0.286 | **0.339** | **0.339** | +0.054 | pure |
| 79 | 2d06bc0a | Real Estate an | docx | 0.803 | 0.818 | **0.833** | +0.030 | v5 |
| 80 | 11593a50 | Real Estate an | pdf | **0.283** | 0.000 | 0.075 | -0.208 | baseline |
| 81 | 1bff4551 | Government | pdf | 0.558 | 0.465 | **0.581** | +0.023 | v5 |
| 82 | 116e791e | Health Care an | pdf | 0.656 | 0.812 | **0.844** | +0.188 | v5 |
| 83 | a69be28f | Wholesale Trad | pdf | **0.208** | 0.198 | 0.000 | -0.208 | baseline |
| 84 | d7cfae6f | Wholesale Trad | xlsx | **0.476** | 0.000 | 0.016 | -0.460 | baseline |
| 85 | 15d37511 | Wholesale Trad | xlsx | 0.101 | **0.248** | 0.220 | +0.119 | pure |
| 86 | 1d4672c8 | Finance and In | pdf,xlsx | 0.492 | 0.000 | **0.508** | +0.017 | v5 |
| 87 | a4a9195c | Manufacturing | docx | 0.419 | **0.500** | 0.484 | +0.065 | pure |
| 88 | 7de33b48 | Professional,  | zip | 0.617 | 0.900 | **0.967** | +0.350 | v5 |
| 89 | 7d7fc9a7 | Professional,  | xlsx | 0.074 | 0.000 | **0.347** | +0.274 | v5 |
| 90 | 27e8912c | Government | docx,pdf | 0.717 | 0.000 | **0.811** | +0.094 | v5 |
| 91 | 38889c3b | Information | zip | 0.032 | **1.000** | 0.871 | +0.839 | pure |
| 92 | 15ddd28d | Manufacturing | docx | 0.579 | 0.579 | **0.632** | +0.053 | v5 |
| 93 | bbe0a93b | Government | pdf | 0.778 | 0.864 | **0.877** | +0.099 | v5 |
| 94 | 2696757c | Government | pdf | 0.366 | **0.488** | 0.463 | +0.098 | pure |
| 95 | 2ea2e5b5 | Professional,  | pptx | 0.000 | **0.318** | 0.188 | +0.188 | pure |
| 96 | f5d428fd | Real Estate an | pdf | **0.792** | 0.736 | 0.585 | -0.208 | baseline |
| 97 | aa071045 | Real Estate an | docx,xlsx | 0.686 | **0.791** | 0.279 | -0.407 | pure |
| 98 | 0ed38524 | Finance and In | pdf | 0.673 | 0.827 | **0.865** | +0.192 | v5 |
| 99 | 9a8c8e28 | Information | pdf | 0.338 | 0.377 | **0.470** | +0.132 | v5 |
| 100 | c94452e4 | Information | - | 0.000 | **0.768** | 0.732 | +0.732 | pure |

_(tied-)best count: pure 52, FBL v5 46, baseline 16 / 100._
