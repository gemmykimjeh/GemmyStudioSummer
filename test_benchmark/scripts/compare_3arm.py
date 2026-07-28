"""3-arm GDPval report: rulebook-only vs FBL v6 vs FBL v6.1 (same 100 tasks).

All three are the SAME agent (`ace_dual_gdpval`) on the same tasks, same file-I/O +
codegen pipeline, same grader. The only variable is what the generator is shown and
what the learning loop is allowed to write:

  - rulebook-only  ACE_SHOW_PLAYBOOK=0 — the immutable 27-rule rulebook is shown, the
                   LEARNED playbook is hidden. Learning still runs in the background
                   (tagging, counting, pruning, curation) — it is just never displayed.
  - FBL v6         rulebook + learned playbook. Growth gate: a task scoring above 0.75
                   still reflects but may not grow the playbook.
  - FBL v6.1       v6 + a ban on rubric-referencing bullets (the generator never sees a
                   rubric, so such lessons are unusable), plus two codegen fixes:
                   respect the script's exit code, and reject empty artifacts.

Usage: python scripts/compare_3arm.py > AB_rulebook_v6_v61.md
"""
import glob, json, os, math, collections, statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
import harness.benchmarks.gdpval as G                      # noqa: E402
from harness.registry import get_benchmark                  # noqa: E402

RUNS = [("rb", "rulebook-only", "runs/gemini_rulebook_only/gdpval__ace_dual_gdpval/results.jsonl"),
        ("v6", "FBL v6",        "runs/gemini_fbl_v6/gdpval__ace_dual_gdpval/results.jsonl"),
        ("v61", "FBL v6.1",     "runs/gemini_fbl_v61/gdpval__ace_dual_gdpval/results.jsonl")]
FBL_LOGS = "ace_dual_gdpval_run/detailed_llm_logs"
# final learned playbooks, matched to each run by completion time
PB = {"rb": ("playbooks/_bak_ace_dual_gdpval_single.txt.1784538997", 104, 37),
      "v6": ("playbooks/_bak_ace_dual_gdpval_single.txt.1784532451", 105, 62),
      "v61": ("playbooks/_bak_ace_dual_gdpval_single.txt.1784594593", 115, 0)}


def load(p):
    return {json.loads(l)["task_id"]: json.loads(l) for l in open(p, encoding="utf-8")}


D = {k: load(p) for k, _, p in RUNS}
order = [t for t in D["v6"] if all(t in d for d in D.values())]
b = get_benchmark("gdpval"); b = b() if isinstance(b, type) else b
tasks = {t.id: t for t in b.load_tasks()}
KEYS = [k for k, _, _ in RUNS]
LBL = {k: l for k, l, _ in RUNS}


def sc(k, t):
    return D[k][t]["score"]


def mt(k, t, f, d=None):
    return (D[k][t].get("metrics") or {}).get(f, d)


def mean(xs):
    return sum(xs) / len(xs)


def h2h(a, c, ks):
    w = sum(1 for t in ks if sc(a, t) > sc(c, t) + 1e-9)
    l = sum(1 for t in ks if sc(c, t) > sc(a, t) + 1e-9)
    n = w + l
    z = (w - n / 2) / ((n * 0.25) ** 0.5) if n else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / 2 ** 0.5))) if n else 1.0
    return w, l, len(ks) - n, p


def brain_tokens(results_path):
    t_end = os.path.getmtime(results_path) + 5
    cand = [f for f in glob.glob(FBL_LOGS + "/generator_*.json") if os.path.getmtime(f) <= t_end]
    gens = sorted(cand, key=os.path.getmtime)[-100:]
    if not gens:
        return {}
    t0 = os.path.getmtime(gens[0]) - 1
    agg = collections.defaultdict(lambda: [0, 0, 0])
    for f in glob.glob(FBL_LOGS + "/*.json"):
        if not (t0 <= os.path.getmtime(f) <= t_end):
            continue
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        r = j.get("role", "?")
        agg[r][0] += 1
        agg[r][1] += j.get("prompt_num_tokens", 0) or 0
        agg[r][2] += j.get("response_num_tokens", 0) or 0
    return agg


def grader_tokens(k):
    calls = pt = rt = 0
    for t in order:
        task = tasks.get(t)
        if not task:
            continue
        rub = (task.metadata or {}).get("rubric") or []
        rjson = json.dumps([{"rubric_item_id": c.get("rubric_item_id"), "score": c.get("score"),
                             "criterion": c.get("criterion"), "required": c.get("required")}
                            for c in rub], ensure_ascii=False)
        calls += 1
        pt += (len(G.GRADER_TEMPLATE) + len(task.prompt) + (mt(k, t, "deliverable_chars", 0) or 0)
               + len(rjson)) // 4
        rt += min(len(rub) * 60, 4096 * 4) // 4
    return calls, pt, rt


P = print
P("# GDPval — rulebook-only vs FBL v6 vs FBL v6.1\n")
P(f"Same {len(order)} gdpval train tasks, same agent (`ace_dual_gdpval`), same file-I/O + "
  "codegen pipeline, same grader, all cold starts. The ONLY variable is what the "
  "generator is shown and what the learning loop may write.\n")
P("- **rulebook-only** — `ACE_SHOW_PLAYBOOK=0`. The immutable 27-rule rulebook is shown; "
  "the LEARNED playbook is hidden from the generator. Learning still runs in the "
  "background (reflect, helpful/harmful tagging, counting, prune, curate) — it is just "
  "never displayed. This isolates *what the learned playbook adds on top of the rulebook*.")
P("- **FBL v6** — rulebook + learned playbook, with the growth gate (a task scoring above "
  "0.75 still reflects but may not grow the playbook).")
P("- **FBL v6.1** — v6 plus: (a) **rubric-referencing bullets banned** — the generator never "
  "sees a rubric, so \"treat the rubric as a schema\" is an instruction it cannot follow; "
  "(b) codegen **respects the script's exit code** (a crash that left a stub file used to "
  "count as success); (c) **empty artifacts rejected**.\n")

P("## 1. Overall scores\n")
P("| metric | rulebook-only | FBL v6 | FBL v6.1 |")
P("|---|---|---|---|")
S = {k: [sc(k, t) for t in order] for k in KEYS}
best = max(mean(S[k]) for k in KEYS)


def mm(k):
    v = mean(S[k])
    return f"**{v:.4f}** 🥇" if abs(v - best) < 1e-9 else f"{v:.4f}"


P(f"| **mean score** | {mm('rb')} | {mm('v6')} | {mm('v61')} |")
P("| median | " + " | ".join(f"{st.median(S[k]):.3f}" for k in KEYS) + " |")
P("| pass ≥0.5 | " + " | ".join(str(sum(x >= .5 for x in S[k])) for k in KEYS) + " |")
P("| good ≥0.8 | " + " | ".join(str(sum(x >= .8 for x in S[k])) for k in KEYS) + " |")
P("| zeros | " + " | ".join(str(sum(x == 0 for x in S[k])) for k in KEYS) + " |")
P("| avg deliverable chars | " + " | ".join(
    str(sum(mt(k, t, "deliverable_chars", 0) or 0 for t in order) // len(order)) for k in KEYS) + " |")
P("| criteria met rate | " + " | ".join(
    f"{mean([(mt(k,t,'n_met',0) or 0)/max(mt(k,t,'n_criteria',1) or 1,1) for t in order]):.3f}"
    for k in KEYS) + " |")

P("\n**Head-to-head**\n")
P("| pair | wins–losses (ties) | Δmean | p |")
P("|---|---|---|---|")
for a, c in [("rb", "v6"), ("rb", "v61"), ("v6", "v61")]:
    w, l, t, p = h2h(a, c, order)
    P(f"| {LBL[a]} vs {LBL[c]} | {w}–{l} ({t}) | {mean(S[a])-mean(S[c]):+.4f} | {p:.3f} |")

P("\n## 2. Reproducibility — how much of the gap is run-to-run variation?\n")
P("The right way to size the noise is to run the SAME configuration twice and see how "
  "much it moves on its own. Both arms were repeated (`*_r2` runs); the difference "
  "between two identical runs is pure variation, because the true effect there is zero.\n")
REP = {"rb": ("runs/gemini_rulebook_only/gdpval__ace_dual_gdpval/results.jsonl",
              "runs/gemini_rulebook_r2/gdpval__ace_dual_gdpval/results.jsonl"),
       "v61": ("runs/gemini_fbl_v61/gdpval__ace_dual_gdpval/results.jsonl",
               "runs/gemini_fbl_v61_r2/gdpval__ace_dual_gdpval/results.jsonl")}
P("| arm | run 1 | run 2 | Δ between identical runs | per-task sd | tasks scoring identically |")
P("|---|---|---|---|---|---|")
rep_stats = {}
for k in ("rb", "v61"):
    p1, p2 = REP[k]
    if not (os.path.exists(p1) and os.path.exists(p2)):
        continue
    A, B = load(p1), load(p2)
    ks = [t for t in A if t in B]
    s1 = [A[t]["score"] for t in ks]
    s2 = [B[t]["score"] for t in ks]
    d = [y - x for x, y in zip(s1, s2)]
    same = sum(1 for x in d if abs(x) < 1e-9)
    rep_stats[k] = (mean(s1), mean(s2), st.pstdev(d), same)
    P(f"| {LBL[k]} | {mean(s1):.4f} | {mean(s2):.4f} | {mean(d):+.4f} | {st.pstdev(d):.3f} "
      f"| {same}/{len(ks)} |")
P("\n**Two things fall out, and they point in opposite directions.**\n")
P("**(a) The means are stable, so the gap between arms is real.** Each arm reproduces its "
  "own mean to within ~0.004. rulebook-only lands at ~0.659 twice; v6.1 lands at ~0.599 "
  "twice. The **~0.060 gap between them is roughly 15× the run-to-run wobble**, so it is "
  "not something that will vanish on the next run.\n")
P("**(b) Per-task stability differs enormously — and that is itself a finding.** "
  "rulebook-only scores *identically* on 93 of 100 tasks across two runs (sd 0.052): with "
  "a fixed 27-rule prompt, the generator behaves almost deterministically. v6.1 repeats "
  "only 31 of 100 (sd 0.209, 4× higher). The reason is structural: the learned playbook "
  "**grows differently on every run** — reflection and curation are stochastic — so by the "
  "middle of a run the two repeats are being shown different prompts. The learning loop's "
  "clearest measurable effect is therefore **added variance**, not a better mean. For a "
  "deployed agent that is a cost in its own right: the same task stops giving the same "
  "answer.\n")
P("> **Correction.** An earlier version of this document filtered out tasks where the "
  "three arms disagreed sharply, calling them \"codegen lottery\", and concluded the arms "
  "were indistinguishable. That was wrong. The repeats above show rulebook-only is nearly "
  "deterministic, so disagreement *between arms* was the condition difference — the real "
  "signal — not noise. Filtering it discarded the effect being measured.\n")
P("Caveat: FBL v6 has only one run, so it has no reproducibility estimate here; its "
  "0.6288 should be read as a single sample.\n")

P("## 3. Mechanism — codegen outcome mix\n")
P("| arm | file produced | no_code (prose) | model failure | env blocked |")
P("|---|---|---|---|---|")
for k in KEYS:
    c = collections.Counter(mt(k, t, "codegen_fail") for t in order)
    P(f"| {LBL[k]} | {c.get('none',0)} | **{c.get('no_code',0)}** | {c.get('model',0)} | {c.get('env',0)} |")
P("\n`no_code` = the task wanted a file but the model wrote prose. v6's learned playbook "
  "pushed the model toward prose (15 cases vs 8); the v6.1 rubric ban removed exactly that "
  "pressure and brought it back to 8 — matching rulebook-only. This is a **directional, "
  "reproducible** effect (in the v6-vs-rulebook pairing it was 7–0 asymmetric), unlike the "
  "score differences above. The mechanism was real and the fix worked — it just did not "
  "move the score.\n")

P("## 4. What the learning loop produced\n")
P("| arm | final playbook | bullets | rubric-referencing bullets | shown to generator? |")
P("|---|---|---|---|---|")
for k in KEYS:
    path, n, rub = PB[k]
    chars = len(open(path, encoding="utf-8").read())
    shown = "no (hidden)" if k == "rb" else "yes"
    P(f"| {LBL[k]} | {chars:,} chars | {n} | {rub} | {shown} |")
P("\nThe rubric ban worked: **62 → 0** contaminated bullets. Note the playbook did not "
  "shrink — v6.1 ended up with *more* bullets (115) in *more* characters than v6, because "
  "the reflector, told not to lean on the rubric, wrote other lessons instead. So v6.1 "
  "tests \"cleaner content, same volume\", not \"less content\".\n")

P("## 5. By sector (mean score)\n")
P("| sector | n | rulebook-only | FBL v6 | FBL v6.1 |")
P("|---|---|---|---|---|")
sec = collections.defaultdict(list)
for t in order:
    sec[(D["v6"][t].get("metrics") or {}).get("sector", "?")].append(t)
for s, ks in sorted(sec.items(), key=lambda kv: -mean([sc("rb", t) for t in kv[1]])):
    ms = {k: mean([sc(k, t) for t in ks]) for k in KEYS}
    win = max(ms.values())
    P(f"| {s} | {len(ks)} | " + " | ".join(
        (f"**{ms[k]:.3f}**" if abs(ms[k]-win) < 1e-9 else f"{ms[k]:.3f}") for k in KEYS) + " |")

P("\n## 6. By gold deliverable type\n")


def kind(t):
    g = mt("v6", t, "gold_types") or []
    if any("xlsx" in x for x in g):
        return "xlsx"
    if any(x in (".docx", ".pdf") for x in g):
        return "docx/pdf"
    return "other file" if g else "prose / none"


P("| gold type | n | rulebook-only | FBL v6 | FBL v6.1 |")
P("|---|---|---|---|---|")
byk = collections.defaultdict(list)
for t in order:
    byk[kind(t)].append(t)
for g, ks in sorted(byk.items(), key=lambda kv: -len(kv[1])):
    ms = {k: mean([sc(k, t) for t in ks]) for k in KEYS}
    P(f"| {g} | {len(ks)} | " + " | ".join(f"{ms[k]:.3f}" for k in KEYS) + " |")
P("\n`xlsx` is the weakest type for every arm (0.42–0.49). Grading GDPval's own gold "
  "spreadsheets through this same pipeline caps out at ~0.80, so part of that is the "
  "measurement, not the agent.\n")

P("## 7. Token usage by role\n")
P("Brain roles are exact (`detailed_llm_logs`, windowed per run by completion time); "
  "`grader*` is reconstructed from lengths (chars/4).\n")
tot = {}
for k, _, path in RUNS:
    agg = brain_tokens(path)
    gc, gp, gr = grader_tokens(k)
    P(f"### {LBL[k]}\n| role | calls | prompt tok | resp tok | total |")
    P("|---|---|---|---|---|")
    tc = tp = tr = 0
    for role in ["generator", "reflector", "curator"]:
        if role in agg:
            c, p, r = agg[role]
            P(f"| {role} | {c} | {p:,} | {r:,} | {p+r:,} |")
            tc += c; tp += p; tr += r
    P(f"| grader* | {gc} | {gp:,} | {gr:,} | {gp+gr:,} |")
    P(f"| **TOTAL** | **{tc+gc}** | **{tp+gp:,}** | **{tr+gr:,}** | **{tp+tr+gp+gr:,}** |\n")
    tot[k] = (tc + gc, tp + tr + gp + gr)
P("| | " + " | ".join(LBL[k] for k in KEYS) + " |")
P("|---|" + "---|" * len(KEYS))
P("| total calls | " + " | ".join(str(tot[k][0]) for k in KEYS) + " |")
P("| total tokens | " + " | ".join(f"{tot[k][1]:,}" for k in KEYS) + " |")
P("\nrulebook-only still pays for the full learning loop (it learns, it just does not "
  "display the result), so its cost is not lower — its generator prompts are simply "
  "much shorter.\n")

P("## 8. All 100 tasks\n")
spread = {t: max(sc(k, t) for k in KEYS) - min(sc(k, t) for k in KEYS) for t in order}
P("`spread` = max−min across the three arms. Per §2 this is **not** a noise measure — "
  "rulebook-only reproduces 93/100 tasks exactly — so a large spread marks a task where "
  "the arms genuinely diverge, i.e. where the condition matters most.\n")
P("| # | task | sector | gold | rulebook-only | FBL v6 | FBL v6.1 | spread |")
P("|---|---|---|---|---|---|---|---|")
for i, t in enumerate(order, 1):
    vals = {k: sc(k, t) for k in KEYS}
    win = max(vals.values())
    gt = ",".join(x.lstrip(".") for x in (mt("v6", t, "gold_types") or [])) or "-"
    sect = ((D["v6"][t].get("metrics") or {}).get("sector") or "?")[:14]
    cells = " | ".join((f"**{vals[k]:.3f}**" if abs(vals[k]-win) < 1e-9 else f"{vals[k]:.3f}")
                       for k in KEYS)
    P(f"| {i} | {t[:8]} | {sect} | {gt} | {cells} | {spread[t]:.2f} |")

P("\n## 9. Caveats\n")
P("- **Run counts differ.** rulebook-only and v6.1 have two runs each (§2); FBL v6 has "
  "one, so its number is a single sample. A third run of each is in progress.")
P("- **The gap is reproducible, the per-task detail is not.** Means repeat to ~0.004, but "
  "v6.1 reproduces only 31/100 individual task scores, so read §8 row-by-row with care — "
  "an individual v6.1 cell may differ on the next run even though the column mean will not.")
P("- **Same 100 tasks throughout.** FBL v2→v6.1 were all iterated against these tasks, so "
  "none of these numbers is evidence of generalisation. A held-out slice is still owed.")
P("- **One model everywhere.** The proxy's `\"*\"` catch-all routes every model name — "
  "including the grader's `claude-sonnet-4-6` — to `gemini-3.1-flash-lite`. Generator, "
  "reflector, curator and grader are the same small model.")
P("- Gold reference files were used only to validate the measuring apparatus (the "
  "achievable ceiling ≈0.81), never to shape agent behaviour.")
