"""Full GDPval (220) 4-arm report: baseline ACE / pure LLM+tools / rulebook-only / FBL v6.3.

The decisive question this run answers: do the effects seen on the FIRST 100 tasks
(which every FBL version was iterated against) hold on tasks 101-220, which NO version
ever saw during development? So every table is split: ALL 220, TUNING (first 100),
HELD-OUT (101-220).
"""
import json, os, collections, statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
import harness.benchmarks.gdpval as G                      # noqa: E402
from harness.registry import get_benchmark                  # noqa: E402

SP = (r"C:\Users\wooja\AppData\Local\Temp\claude\C--GemmyStudioSummer"
      r"\83c0eccb-4e41-4331-9ab7-755da18bf404\scratchpad")
ARMS = [("base", "baseline ACE", "runs/gemini_baseline_220/gdpval__ace_gdpval/results.jsonl"),
        ("pure", "pure LLM+tools", os.path.join(SP, "pure220_tools.jsonl")),
        ("rb", "rulebook-only", "runs/gemini_rulebook_220/gdpval__ace_dual_gdpval/results.jsonl"),
        ("v63", "FBL v6.3", "runs/gemini_v63_220/gdpval__ace_dual_gdpval/results.jsonl")]


def load(p):
    return {json.loads(l)["task_id"]: json.loads(l) for l in open(p, encoding="utf-8")}


D = {k: load(p) for k, _, p in ARMS}
LBL = {k: l for k, l, _ in ARMS}
KEYS = [k for k, _, _ in ARMS]
# dataset order from baseline file (all arms share the same task set/order)
order = list(load(ARMS[0][2]).keys())
order = [t for t in order if all(t in D[k] for k in KEYS)]
b = get_benchmark("gdpval"); b = b() if isinstance(b, type) else b
tasks = {t.id: t for t in b.load_tasks()}


def sc(k, t):
    return D[k][t].get("score", 0.0)


def mt(k, t, f, d=None):
    return (D[k][t].get("metrics") or {}).get(f, d)


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def block(ks, title):
    print(f"\n### {title}  (n={len(ks)})\n")
    print("| metric | baseline | pure+tools | rulebook | FBL v6.3 |")
    print("|---|---|---|---|---|")
    S = {k: [sc(k, t) for t in ks] for k in KEYS}
    best = max(mean(S[k]) for k in KEYS)
    row = []
    for k in KEYS:
        v = mean(S[k])
        row.append(f"**{v:.4f}** \U0001F947" if abs(v - best) < 1e-9 else f"{v:.4f}")
    print(f"| **mean** | {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
    print("| pass ≥0.5 | " + " | ".join(str(sum(x >= .5 for x in S[k])) for k in KEYS) + " |")
    print("| good ≥0.8 | " + " | ".join(str(sum(x >= .8 for x in S[k])) for k in KEYS) + " |")
    print("| zeros | " + " | ".join(str(sum(x == 0 for x in S[k])) for k in KEYS) + " |")
    return {k: mean(S[k]) for k in KEYS}


P = print
P("# GDPval FULL 220 — 4-arm comparison\n")
P("baseline ACE, pure LLM+tools, rulebook-only, FBL v6.3 — same 220 tasks, same file-I/O + "
  "codegen pipeline, same grader (rotation proxy). All data integrity-checked (220 unique "
  "each, 0 duplicates, no quota-corruption).\n")
P("**Split that matters:** every FBL version (v2→v6.3) and the rulebook were iterated "
  "against the FIRST 100 tasks. Tasks 101–220 are effectively HELD-OUT — never seen during "
  "development. If an arm's ranking holds there, it generalizes; if it collapses, it was "
  "overfitting.\n")

P("## 1. Overall & held-out split")
allm = block(order, "ALL 220")
tune = block(order[:100], "TUNING (first 100)")
held = block(order[100:], "HELD-OUT (tasks 101–220)")

P("\n**Generalization check (mean):**\n")
P("| arm | tuning(1-100) | held-out(101-220) | drop |")
P("|---|---|---|---|")
for k in KEYS:
    P(f"| {LBL[k]} | {tune[k]:.4f} | {held[k]:.4f} | {tune[k]-held[k]:+.4f} |")
P("\nA large positive drop = that arm did better on the tasks it was tuned against than on "
  "fresh ones (overfitting signature). Compare drops across arms: baseline/pure were NOT tuned "
  "on these tasks, so their drop is the natural task-difficulty difference between the two "
  "halves; any FBL/rulebook drop beyond that baseline drop is fitting.\n")

P("## 2. Head-to-head on HELD-OUT (the honest ranking)\n")
held_keys = order[100:]
P("| pair | wins–losses | Δmean |")
P("|---|---|---|")
import itertools
for a, c in itertools.combinations(KEYS, 2):
    w = sum(1 for t in held_keys if sc(a, t) > sc(c, t) + 1e-9)
    l = sum(1 for t in held_keys if sc(c, t) > sc(a, t) + 1e-9)
    P(f"| {LBL[a]} vs {LBL[c]} | {w}–{l} | {mean([sc(a,t) for t in held_keys])-mean([sc(c,t) for t in held_keys]):+.4f} |")

P("\n## 3. By gold deliverable type (ALL 220)\n")
def kind(t):
    g = mt("v63", t, "gold_types") or []
    if any("xlsx" in x for x in g):
        return "xlsx"
    if any(x in (".docx", ".pdf") for x in g):
        return "docx/pdf"
    return "other-file" if g else "prose/none"
byk = collections.defaultdict(list)
for t in order:
    byk[kind(t)].append(t)
P("| gold | n | baseline | pure | rulebook | FBL v6.3 |")
P("|---|---|---|---|---|---|")
for g, ks in sorted(byk.items(), key=lambda kv: -len(kv[1])):
    P(f"| {g} | {len(ks)} | " + " | ".join(f"{mean([sc(k,t) for t in ks]):.3f}" for k in KEYS) + " |")

P("\n## 4. By sector (ALL 220, mean)\n")
sec = collections.defaultdict(list)
for t in order:
    sec[(D["base"][t].get("metrics") or {}).get("sector", "?")].append(t)
P("| sector | n | baseline | pure | rulebook | FBL v6.3 |")
P("|---|---|---|---|---|---|")
for s, ks in sorted(sec.items(), key=lambda kv: -mean([sc('rb', t) for t in kv[1]])):
    P(f"| {s} | {len(ks)} | " + " | ".join(f"{mean([sc(k,t) for t in ks]):.3f}" for k in KEYS) + " |")

P("\n## 5. Caveats\n")
P("- **Single run per arm** (compute/quota limits). Earlier 3-run repeats on the first 100 "
  "showed run-to-run mean sd ≈ 0.003 for rulebook and ≈ 0.013 for FBL (learning adds "
  "variance), so treat gaps under ~0.02 as provisional.")
P("- **One model everywhere** — generator, reflector, curator, and grader all resolve to "
  "gemini-3.1-flash-lite through the proxy's catch-all route.")
P("- Gold reference files were used only to validate the measuring apparatus, never to shape "
  "any agent's behaviour.")
