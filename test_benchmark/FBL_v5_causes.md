# FBL v5 — cause analysis of ALL 100 tasks (pass + fail)

Source: `runs/gemini_fbl_v5_final/gdpval__ace_dual_gdpval/results.jsonl` (100 tasks). Every task is classified by primary cause; passing tasks additionally get a **grader-suspect** audit (a 1.0 can be judge leniency on a thin/vague rubric, not proof of quality).

## 0. Headline

- mean score **0.6037**, pass≥0.5 **71**, good≥0.8 **30**, perfect(1.0) **2**, zeros **4**

## 1. Primary-cause buckets (every task)

| cause | n | meaning |
|---|---|---|
| content:other | 25 | file made, misc criteria unmet |
| high_pass | 24 | ≥0.8, most criteria met |
| content:format/structure | 18 | file made, format/layout criteria unmet |
| content:data/value/calc | 14 | file made, wrong/again values — the hard core (runs-but-wrong) |
| no_code_prose | 13 | file task but prose emitted |
| env_blocked | 2 | OS/tooling blocked (not model) — content-graded |
| perfect | 2 | all criteria met (audit for grader leniency below) |
| content:content/analysis | 1 | file made, analysis/explanation criteria unmet |
| content:completeness/enum | 1 | file made, missed enumerating every member of a set |

## 2. Where the points are lost (missed-criterion points by category)

| missed category | total points lost |
|---|---|
| format/structure | 950 |
| data/value/calc | 853 |
| other | 820 |
| completeness/enum | 241 |
| content/analysis | 117 |

## 3. Grader-suspect tasks (score may not reflect true quality)

13 tasks flagged. These are where the GRADER, not the model, is the likely swing factor — over-credit (thin/vague rubric perfect) or under-credit (spurious 0).

| task | score | why suspect |
|---|---|---|
| 46bc7238 | 0.836 | 1 short/vague criterion(s) missed — judge-subjective |
| 9a0d8d36 | 0.808 | 1 short/vague criterion(s) missed — judge-subjective |
| 8384083a | 0.746 | 1 short/vague criterion(s) missed — judge-subjective |
| c94452e4 | 0.732 | 1 short/vague criterion(s) missed — judge-subjective |
| a97369c7 | 0.706 | 1 short/vague criterion(s) missed — judge-subjective |
| 0fad6023 | 0.697 | 1 short/vague criterion(s) missed — judge-subjective |
| f5d428fd | 0.585 | 1 short/vague criterion(s) missed — judge-subjective |
| a10ec48c | 0.514 | 1 short/vague criterion(s) missed — judge-subjective |
| aa071045 | 0.279 | 2 short/vague criterion(s) missed — judge-subjective |
| 11593a50 | 0.075 | 1 short/vague criterion(s) missed — judge-subjective |
| 5349dd7b | 0.000 | zero despite a substantial deliverable (2712 chars) — possible spurious 0 |
| 7b08cd4d | 0.000 | zero despite a substantial deliverable (3303 chars) — possible spurious 0 |
| 5f6c57dd | 0.000 | zero despite a substantial deliverable (2773 chars) — possible spurious 0; 3 short/vague criterion(s) missed — judge-subjective |

## 4. Per-task cause line-up

| # | task | sector | score | gold | codegen | cause | grader-suspect |
|---|---|---|---|---|---|---|---|
| 1 | 83d10b06 | Professional,  | 0.460 | xlsx | none | content:format/structure |  |
| 2 | f84ea6ac | Government | 0.879 | docx | none | high_pass |  |
| 3 | 99ac6944 | Information | 0.732 | pdf | none | content:other |  |
| 4 | 1b1ade2d | Manufacturing | 0.580 | docx | no_code | no_code_prose |  |
| 5 | 575f8679 | Government | 0.877 | - | none | high_pass |  |
| 6 | 36d567ba | Government | 0.741 | docx | no_code | no_code_prose |  |
| 7 | cebf301e | Professional,  | 0.919 | docx | none | high_pass |  |
| 8 | a10ec48c | Real Estate an | 0.514 | docx | none | content:format/structure | ⚠ |
| 9 | a0ef404e | Real Estate an | 0.810 | docx | none | high_pass |  |
| 10 | f3351922 | Finance and In | 0.694 | - | no_code | content:other |  |
| 11 | 401a07f1 | Information | 0.697 | - | none | content:format/structure |  |
| 12 | 8c8fc328 | Information | 0.980 | docx | none | high_pass |  |
| 13 | a1963a68 | Finance and In | 0.636 | - | none | content:other |  |
| 14 | 8079e27d | Finance and In | 0.618 | - | none | content:format/structure |  |
| 15 | ec591973 | Wholesale Trad | 0.506 | pptx | none | content:other |  |
| 16 | 6dcae3f5 | Health Care an | 0.030 | docx,xlsx | none | content:data/value/calc |  |
| 17 | 8c823e32 | Government | 0.825 | - | none | high_pass |  |
| 18 | bf68f2ad | Manufacturing | 0.339 | xlsx | none | content:data/value/calc |  |
| 19 | bd72994f | Retail Trade | 0.862 | - | none | high_pass |  |
| 20 | 8f9e8bcd | Retail Trade | 0.980 | docx | none | high_pass |  |
| 21 | 8a7b6fca | Manufacturing | 0.600 | pdf | none | content:format/structure |  |
| 22 | cd9efc18 | Professional,  | 0.743 | - | none | content:other |  |
| 23 | 5e2b6aab | Manufacturing | 0.632 | pdf,zip | env | env_blocked |  |
| 24 | f1be6436 | Health Care an | 0.365 | docx | none | content:data/value/calc |  |
| 25 | 74d6e8b0 | Health Care an | 0.246 | docx | none | content:other |  |
| 26 | 60221cd0 | Information | 0.655 | pdf | none | content:other |  |
| 27 | 1a78e076 | Health Care an | 0.741 | docx | no_code | no_code_prose |  |
| 28 | b5d2e6f1 | Wholesale Trad | 0.323 | xlsx | none | content:format/structure |  |
| 29 | 9a0d8d36 | Finance and In | 0.808 | pptx | none | high_pass | ⚠ |
| 30 | 91060ff0 | Retail Trade | 0.945 | - | none | high_pass |  |
| 31 | ae0c1093 | Retail Trade | 0.727 | pdf | none | content:format/structure |  |
| 32 | 6241e678 | Information | 0.896 | pdf | none | high_pass |  |
| 33 | 02aa1805 | Professional,  | 0.651 | xlsx | none | content:format/structure |  |
| 34 | a99d85fc | Real Estate an | 0.545 | xlsx | none | content:data/value/calc |  |
| 35 | 46bc7238 | Real Estate an | 0.836 | pdf | none | high_pass | ⚠ |
| 36 | 5ad0c554 | Real Estate an | 0.302 | docx | none | content:format/structure |  |
| 37 | 403b9234 | Government | 0.981 | pptx | none | high_pass |  |
| 38 | 0ec25916 | Health Care an | 0.836 | pdf | none | high_pass |  |
| 39 | b3573f20 | Wholesale Trad | 0.595 | pdf | no_code | no_code_prose |  |
| 40 | ab81b076 | Wholesale Trad | 0.500 | pdf | none | content:other |  |
| 41 | b57efde3 | Wholesale Trad | 0.213 | xlsx | none | content:other |  |
| 42 | 9efbcd35 | Finance and In | 0.618 | - | no_code | content:other |  |
| 43 | 5349dd7b | Manufacturing | 0.000 | xlsx | none | content:data/value/calc | ⚠ |
| 44 | 0e386e32 | Professional,  | 0.590 | zip | none | content:other |  |
| 45 | 7b08cd4d | Professional,  | 0.000 | xlsx | none | content:data/value/calc | ⚠ |
| 46 | a328feea | Government | 0.917 | docx | no_code | no_code_prose |  |
| 47 | f9a1c16c | Information | 0.709 | pdf | none | content:format/structure |  |
| 48 | 93b336f3 | Manufacturing | 0.697 | docx | no_code | no_code_prose |  |
| 49 | a74ead3b | Government | 0.412 | pptx | none | content:other |  |
| 50 | 7bbfcfe9 | Government | 0.189 | xlsx | none | content:other |  |
| 51 | c2e8f271 | Professional,  | 0.457 | docx | none | content:other |  |
| 52 | fccaa4a1 | Real Estate an | 0.741 | pdf | none | content:other |  |
| 53 | b7a5912e | Real Estate an | 0.053 | xlsx | none | content:data/value/calc |  |
| 54 | 61717508 | Finance and In | 0.578 | pdf | none | content:other |  |
| 55 | afe56d05 | Information | 0.590 | - | none | content:format/structure |  |
| 56 | e222075d | Information | 0.738 | mp4 | no_code | no_code_prose |  |
| 57 | 5f6c57dd | Finance and In | 0.000 | - | none | content:data/value/calc | ⚠ |
| 58 | e21cd746 | Finance and In | 0.846 | pdf | none | high_pass |  |
| 59 | 62f04c2f | Wholesale Trad | 0.396 | docx,xlsx | none | content:other |  |
| 60 | 1aecc095 | Health Care an | 0.756 | docx | none | content:other |  |
| 61 | eb54f575 | Government | 0.667 | pdf | none | content:data/value/calc |  |
| 62 | efca245f | Manufacturing | 0.020 | xlsx | none | content:data/value/calc |  |
| 63 | 211d0093 | Retail Trade | 0.961 | pdf | no_code | no_code_prose |  |
| 64 | 0fad6023 | Retail Trade | 0.697 | xlsx | none | content:format/structure | ⚠ |
| 65 | 40a99a31 | Manufacturing | 0.691 | pdf,xlsx | none | content:other |  |
| 66 | a97369c7 | Professional,  | 0.706 | docx | no_code | no_code_prose | ⚠ |
| 67 | 46fc494e | Manufacturing | 1.000 | pdf,png,py | none | perfect |  |
| 68 | 41f6ef59 | Health Care an | 0.864 | docx,xlsx | none | high_pass |  |
| 69 | 81db15ff | Health Care an | 0.783 | xlsx | none | content:content/analysis |  |
| 70 | ef8719da | Information | 1.000 | - | none | perfect |  |
| 71 | 1b9ec237 | Health Care an | 0.831 | pptx | none | high_pass |  |
| 72 | f841ddcf | Wholesale Trad | 0.375 | xlsx | none | content:format/structure |  |
| 73 | 664a42e5 | Finance and In | 0.840 | pptx | none | high_pass |  |
| 74 | 8384083a | Retail Trade | 0.746 | pdf | no_code | no_code_prose | ⚠ |
| 75 | f9f82549 | Retail Trade | 0.636 | pdf | none | content:other |  |
| 76 | e14e32ba | Information | 0.655 | - | none | content:format/structure |  |
| 77 | fd6129bd | Professional,  | 0.802 | docx,pdf | no_code | no_code_prose |  |
| 78 | 55ddb773 | Real Estate an | 0.339 | - | none | content:format/structure |  |
| 79 | 2d06bc0a | Real Estate an | 0.833 | docx | none | high_pass |  |
| 80 | 11593a50 | Real Estate an | 0.075 | pdf | none | content:completeness/enum | ⚠ |
| 81 | 1bff4551 | Government | 0.581 | pdf | none | content:other |  |
| 82 | 116e791e | Health Care an | 0.844 | pdf | none | high_pass |  |
| 83 | a69be28f | Wholesale Trad | 0.000 | pdf | none | content:format/structure |  |
| 84 | d7cfae6f | Wholesale Trad | 0.016 | xlsx | none | content:data/value/calc |  |
| 85 | 15d37511 | Wholesale Trad | 0.220 | xlsx | none | content:data/value/calc |  |
| 86 | 1d4672c8 | Finance and In | 0.508 | pdf,xlsx | none | content:format/structure |  |
| 87 | a4a9195c | Manufacturing | 0.484 | docx | none | content:other |  |
| 88 | 7de33b48 | Professional,  | 0.967 | zip | none | high_pass |  |
| 89 | 7d7fc9a7 | Professional,  | 0.347 | xlsx | none | content:data/value/calc |  |
| 90 | 27e8912c | Government | 0.811 | docx,pdf | none | high_pass |  |
| 91 | 38889c3b | Information | 0.871 | zip | env | env_blocked |  |
| 92 | 15ddd28d | Manufacturing | 0.632 | docx | no_code | no_code_prose |  |
| 93 | bbe0a93b | Government | 0.877 | pdf | none | high_pass |  |
| 94 | 2696757c | Government | 0.463 | pdf | none | content:other |  |
| 95 | 2ea2e5b5 | Professional,  | 0.188 | pptx | none | content:data/value/calc |  |
| 96 | f5d428fd | Real Estate an | 0.585 | pdf | none | content:format/structure | ⚠ |
| 97 | aa071045 | Real Estate an | 0.279 | docx,xlsx | none | content:other | ⚠ |
| 98 | 0ed38524 | Finance and In | 0.865 | pdf | none | high_pass |  |
| 99 | 9a8c8e28 | Information | 0.470 | pdf | no_code | no_code_prose |  |
| 100 | c94452e4 | Information | 0.732 | - | no_code | content:other | ⚠ |

---

## 5. Grader-deficiency verification — re-graded the zeros (the user's concern)

The 4 zeros all had the spurious-0 signature (`n_met=0` across 54–137 criteria on a
produced file, `required_ok=True`). Per the request ("a 1.0 could be grader
deficiency"), I reconstructed each deliverable from its generator log and
**re-graded with the robust grader** to separate grader failure from real failure:

| task | source files | n_criteria | re-graded | verdict |
|---|---|---|---|---|
| 5349dd7b | **0 (none)** | 94 | 0.000 (n_met 0) | GENUINE — no source; task needs real USPS/FedEx/UPS rate data the model lacks, so it **fabricated** values that match none of the 94 specific required tables |
| 7b08cd4d | 1 (Fall Music Tour) | 59 | 0.000 (n_met 0) | GENUINE — source present but built the wrong artifact/structure |
| 5f6c57dd | 1 (Branch Profitability) | 137 | 0.000 (n_met 0) | GENUINE — 137-criterion deliverable, produced structure matches none |
| a69be28f | 1 (Territory Fit) | 54 | **0.188** (n_met 13) | partly spurious — re-graded nonzero; run-to-run variance |

**Conclusion: the grader is NOT deficient on the low end.** 3 of 4 zeros re-grade to
0 consistently and are real content failures (wrong/fabricated artifact against a
large, highly-specific rubric); only 1 showed grader/codegen variance (0→0.188).
So FBL v5's 0.6037 is essentially honest — it is not being robbed by spurious zeros.

On the high end: the 2 perfect(1.0) tasks were NOT flagged as thin-rubric (≥4
criteria, substantial deliverables) → genuine, not judge leniency. The 10
"1 short/vague criterion missed" flags are ±1-criterion judge subjectivity (minor),
not systematic over/under-crediting.

### What this leaves as the real remaining failure modes (model, not grader)
1. **Wrong/fabricated artifact vs a large specific rubric** (the 3 zeros; the s_43
   "no source → guessed the data" case is the worst) — runs-but-wrong, the hard core.
2. **13 no_code_prose** — file deliverable but the model emitted prose, not code
   (rb-fmt-08 compliance gap on flash-lite).
3. **format/structure (950 pts) + data/value/calc (853 pts)** dominate the points
   lost among tasks that DID produce a file — content-correctness, model-reasoning bound.
