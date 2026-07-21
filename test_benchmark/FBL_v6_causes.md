# FBL v6 — cause analysis of ALL 100 tasks (pass + fail)

Source: `runs/gemini_fbl_v6/gdpval__ace_dual_gdpval/results.jsonl` (100 tasks). Every task is classified by primary cause; passing tasks additionally get a **grader-suspect** audit (a 1.0 can be judge leniency on a thin/vague rubric, not proof of quality).

## 0. Headline

- mean score **0.6288**, pass≥0.5 **71**, good≥0.8 **30**, perfect(1.0) **2**, zeros **4**

## 1. Primary-cause buckets (every task)

| cause | n | meaning |
|---|---|---|
| content:other | 29 | file made, misc criteria unmet |
| high_pass | 26 | ≥0.8, most criteria met |
| content:format/structure | 12 | file made, format/layout criteria unmet |
| no_code_prose | 12 | file task but prose emitted |
| content:data/value/calc | 10 | file made, wrong/again values — the hard core (runs-but-wrong) |
| code_broken | 3 | buggy script, no file (repair loop exhausted) — model ability |
| content:completeness/enum | 2 | file made, missed enumerating every member of a set |
| perfect | 2 | all criteria met (audit for grader leniency below) |
| env_blocked | 2 | OS/tooling blocked (not model) — content-graded |
| content:content/analysis | 2 | file made, analysis/explanation criteria unmet |

## 2. Where the points are lost (missed-criterion points by category)

| missed category | total points lost |
|---|---|
| format/structure | 880 |
| data/value/calc | 800 |
| other | 795 |
| completeness/enum | 240 |
| content/analysis | 114 |

## 3. Grader-suspect tasks (score may not reflect true quality)

11 tasks flagged. These are where the GRADER, not the model, is the likely swing factor — over-credit (thin/vague rubric perfect) or under-credit (spurious 0).

| task | score | why suspect |
|---|---|---|
| 91060ff0 | 0.909 | 1 short/vague criterion(s) missed — judge-subjective |
| 46bc7238 | 0.866 | 1 short/vague criterion(s) missed — judge-subjective |
| 0fad6023 | 0.697 | 1 short/vague criterion(s) missed — judge-subjective |
| 9a0d8d36 | 0.692 | 1 short/vague criterion(s) missed — judge-subjective |
| a97369c7 | 0.667 | 1 short/vague criterion(s) missed — judge-subjective |
| 46fc494e | 0.462 | 5 short/vague criterion(s) missed — judge-subjective |
| c94452e4 | 0.393 | 2 short/vague criterion(s) missed — judge-subjective |
| aa071045 | 0.279 | 2 short/vague criterion(s) missed — judge-subjective |
| 11593a50 | 0.075 | 1 short/vague criterion(s) missed — judge-subjective |
| 5349dd7b | 0.000 | zero despite a substantial deliverable (2902 chars) — possible spurious 0 |
| 5f6c57dd | 0.000 | 3 short/vague criterion(s) missed — judge-subjective |

## 4. Per-task cause line-up

| # | task | sector | score | gold | codegen | cause | grader-suspect |
|---|---|---|---|---|---|---|---|
| 1 | 83d10b06 | Professional,  | 0.730 | xlsx | none | content:format/structure |  |
| 2 | f84ea6ac | Government | 0.845 | docx | none | high_pass |  |
| 3 | 99ac6944 | Information | 0.744 | pdf | none | content:other |  |
| 4 | 1b1ade2d | Manufacturing | 0.638 | docx | no_code | no_code_prose |  |
| 5 | 575f8679 | Government | 0.908 | - | none | high_pass |  |
| 6 | 36d567ba | Government | 0.741 | docx | no_code | no_code_prose |  |
| 7 | cebf301e | Professional,  | 0.919 | docx | none | high_pass |  |
| 8 | a10ec48c | Real Estate an | 0.657 | docx | none | content:format/structure |  |
| 9 | a0ef404e | Real Estate an | 0.759 | docx | none | content:other |  |
| 10 | f3351922 | Finance and In | 0.706 | - | no_code | content:other |  |
| 11 | 401a07f1 | Information | 0.909 | - | none | high_pass |  |
| 12 | 8c8fc328 | Information | 0.980 | docx | none | high_pass |  |
| 13 | a1963a68 | Finance and In | 0.652 | - | none | content:other |  |
| 14 | 8079e27d | Finance and In | 0.636 | - | none | content:format/structure |  |
| 15 | ec591973 | Wholesale Trad | 0.481 | pptx | none | content:other |  |
| 16 | 6dcae3f5 | Health Care an | 0.030 | docx,xlsx | none | content:data/value/calc |  |
| 17 | 8c823e32 | Government | 0.817 | - | none | high_pass |  |
| 18 | bf68f2ad | Manufacturing | 0.393 | xlsx | none | content:data/value/calc |  |
| 19 | bd72994f | Retail Trade | 0.793 | - | none | content:completeness/enum |  |
| 20 | 8f9e8bcd | Retail Trade | 1.000 | docx | none | perfect |  |
| 21 | 8a7b6fca | Manufacturing | 0.425 | pdf | none | content:other |  |
| 22 | cd9efc18 | Professional,  | 0.750 | - | none | content:other |  |
| 23 | 5e2b6aab | Manufacturing | 0.706 | pdf,zip | env | env_blocked |  |
| 24 | f1be6436 | Health Care an | 0.378 | docx | none | content:data/value/calc |  |
| 25 | 74d6e8b0 | Health Care an | 0.304 | docx | none | content:other |  |
| 26 | 60221cd0 | Information | 0.655 | pdf | none | content:other |  |
| 27 | 1a78e076 | Health Care an | 0.906 | docx | no_code | no_code_prose |  |
| 28 | b5d2e6f1 | Wholesale Trad | 0.338 | xlsx | none | content:format/structure |  |
| 29 | 9a0d8d36 | Finance and In | 0.692 | pptx | none | content:other | ⚠ |
| 30 | 91060ff0 | Retail Trade | 0.909 | - | none | high_pass | ⚠ |
| 31 | ae0c1093 | Retail Trade | 0.818 | pdf | none | high_pass |  |
| 32 | 6241e678 | Information | 0.620 | pdf | no_code | no_code_prose |  |
| 33 | 02aa1805 | Professional,  | 0.651 | xlsx | none | content:format/structure |  |
| 34 | a99d85fc | Real Estate an | 0.961 | xlsx | none | high_pass |  |
| 35 | 46bc7238 | Real Estate an | 0.866 | pdf | none | high_pass | ⚠ |
| 36 | 5ad0c554 | Real Estate an | 0.143 | docx | none | content:format/structure |  |
| 37 | 403b9234 | Government | 0.981 | pptx | none | high_pass |  |
| 38 | 0ec25916 | Health Care an | 0.836 | pdf | none | high_pass |  |
| 39 | b3573f20 | Wholesale Trad | 0.784 | pdf | no_code | no_code_prose |  |
| 40 | ab81b076 | Wholesale Trad | 0.596 | pdf | none | content:other |  |
| 41 | b57efde3 | Wholesale Trad | 0.213 | xlsx | none | content:other |  |
| 42 | 9efbcd35 | Finance and In | 0.658 | - | no_code | content:other |  |
| 43 | 5349dd7b | Manufacturing | 0.000 | xlsx | none | content:data/value/calc | ⚠ |
| 44 | 0e386e32 | Professional,  | 0.538 | zip | none | content:other |  |
| 45 | 7b08cd4d | Professional,  | 0.000 | xlsx | model | code_broken |  |
| 46 | a328feea | Government | 0.917 | docx | none | high_pass |  |
| 47 | f9a1c16c | Information | 0.987 | pdf | none | high_pass |  |
| 48 | 93b336f3 | Manufacturing | 0.737 | docx | no_code | no_code_prose |  |
| 49 | a74ead3b | Government | 0.412 | pptx | none | content:other |  |
| 50 | 7bbfcfe9 | Government | 0.189 | xlsx | none | content:other |  |
| 51 | c2e8f271 | Professional,  | 0.531 | docx | none | content:other |  |
| 52 | fccaa4a1 | Real Estate an | 0.685 | pdf | none | content:other |  |
| 53 | b7a5912e | Real Estate an | 0.605 | xlsx | no_code | no_code_prose |  |
| 54 | 61717508 | Finance and In | 0.891 | pdf | none | high_pass |  |
| 55 | afe56d05 | Information | 0.521 | - | none | content:format/structure |  |
| 56 | e222075d | Information | 0.770 | mp4 | no_code | no_code_prose |  |
| 57 | 5f6c57dd | Finance and In | 0.000 | - | model | code_broken | ⚠ |
| 58 | e21cd746 | Finance and In | 0.872 | pdf | none | high_pass |  |
| 59 | 62f04c2f | Wholesale Trad | 0.396 | docx,xlsx | none | content:other |  |
| 60 | 1aecc095 | Health Care an | 0.600 | docx | no_code | no_code_prose |  |
| 61 | eb54f575 | Government | 0.841 | pdf | none | high_pass |  |
| 62 | efca245f | Manufacturing | 0.539 | xlsx | none | content:data/value/calc |  |
| 63 | 211d0093 | Retail Trade | 0.980 | pdf | none | high_pass |  |
| 64 | 0fad6023 | Retail Trade | 0.697 | xlsx | none | content:format/structure | ⚠ |
| 65 | 40a99a31 | Manufacturing | 0.753 | pdf,xlsx | none | content:other |  |
| 66 | a97369c7 | Professional,  | 0.667 | docx | no_code | no_code_prose | ⚠ |
| 67 | 46fc494e | Manufacturing | 0.462 | pdf,png,py | none | content:data/value/calc | ⚠ |
| 68 | 41f6ef59 | Health Care an | 0.833 | docx,xlsx | none | high_pass |  |
| 69 | 81db15ff | Health Care an | 0.783 | xlsx | none | content:content/analysis |  |
| 70 | ef8719da | Information | 1.000 | - | none | perfect |  |
| 71 | 1b9ec237 | Health Care an | 0.864 | pptx | none | high_pass |  |
| 72 | f841ddcf | Wholesale Trad | 0.023 | xlsx | none | content:format/structure |  |
| 73 | 664a42e5 | Finance and In | 0.820 | pptx | none | high_pass |  |
| 74 | 8384083a | Retail Trade | 0.714 | pdf | none | content:data/value/calc |  |
| 75 | f9f82549 | Retail Trade | 0.697 | pdf | none | content:other |  |
| 76 | e14e32ba | Information | 0.655 | - | none | content:format/structure |  |
| 77 | fd6129bd | Professional,  | 0.814 | docx,pdf | no_code | no_code_prose |  |
| 78 | 55ddb773 | Real Estate an | 0.429 | - | none | content:format/structure |  |
| 79 | 2d06bc0a | Real Estate an | 0.864 | docx | none | high_pass |  |
| 80 | 11593a50 | Real Estate an | 0.075 | pdf | none | content:completeness/enum | ⚠ |
| 81 | 1bff4551 | Government | 0.395 | pdf | none | content:other |  |
| 82 | 116e791e | Health Care an | 0.766 | pdf | none | content:other |  |
| 83 | a69be28f | Wholesale Trad | 0.228 | pdf | no_code | no_code_prose |  |
| 84 | d7cfae6f | Wholesale Trad | 0.000 | xlsx | model | code_broken |  |
| 85 | 15d37511 | Wholesale Trad | 0.239 | xlsx | none | content:data/value/calc |  |
| 86 | 1d4672c8 | Finance and In | 0.932 | pdf,xlsx | none | high_pass |  |
| 87 | a4a9195c | Manufacturing | 0.548 | docx | none | content:other |  |
| 88 | 7de33b48 | Professional,  | 0.967 | zip | none | high_pass |  |
| 89 | 7d7fc9a7 | Professional,  | 0.474 | xlsx | none | content:data/value/calc |  |
| 90 | 27e8912c | Government | 0.774 | docx,pdf | none | content:content/analysis |  |
| 91 | 38889c3b | Information | 0.790 | zip | env | env_blocked |  |
| 92 | 15ddd28d | Manufacturing | 0.579 | docx | none | content:other |  |
| 93 | bbe0a93b | Government | 0.877 | pdf | none | high_pass |  |
| 94 | 2696757c | Government | 0.463 | pdf | none | content:other |  |
| 95 | 2ea2e5b5 | Professional,  | 0.459 | pptx | none | content:data/value/calc |  |
| 96 | f5d428fd | Real Estate an | 0.717 | pdf | none | content:format/structure |  |
| 97 | aa071045 | Real Estate an | 0.279 | docx,xlsx | none | content:other | ⚠ |
| 98 | 0ed38524 | Finance and In | 0.865 | pdf | none | high_pass |  |
| 99 | 9a8c8e28 | Information | 0.444 | pdf | none | content:other |  |
| 100 | c94452e4 | Information | 0.393 | - | no_code | content:other | ⚠ |
