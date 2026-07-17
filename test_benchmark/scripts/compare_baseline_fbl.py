"""4-way GDPval comparison: baseline (ACE) vs pure LLM+files vs FBL v3 vs FBL v4.
All share the same 100 gdpval tasks, file-I/O, Gemini generator + Gemini grader (proxy).
The only thing that changes is the scaffolding around the generator.
Emits markdown: overall / by-sector / tokens-by-role / full task 1..100 line-up.
Usage: python scripts/compare_baseline_fbl.py > AB_cold_4way.md
"""
import glob, json, os, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
import harness.benchmarks.gdpval as G           # noqa: E402
from harness.registry import get_benchmark       # noqa: E402
from harness.benchmarks.gdpval_files import reference_files_text  # noqa: E402

PURE = (r"C:\Users\wooja\AppData\Local\Temp\claude\C--GemmyStudioSummer"
        r"\83c0eccb-4e41-4331-9ab7-755da18bf404\scratchpad\pure100_files.jsonl")
BASE = "runs/gemini_baseline_cold/gdpval__ace_gdpval/results.jsonl"
V3   = "runs/gemini_fbl_100_v3/gdpval__ace_dual_gdpval/results.jsonl"
V4   = "runs/gemini_fbl_100_v4/gdpval__ace_dual_gdpval/results.jsonl"
BASE_LOGS, FBL_LOGS = "ace_gdpval_run/detailed_llm_logs", "ace_dual_gdpval_run/detailed_llm_logs"
PURE_SYS = ("You are completing a real professional work task. Produce the COMPLETE "
            "requested deliverable as your response (the full document/analysis/table), "
            "not a summary or plan. Be thorough; you are graded against a detailed rubric.")


def load(p):
    return {json.loads(l)["task_id"]: json.loads(l) for l in open(p, encoding="utf-8")}

pure, base, v3, v4 = load(PURE), load(BASE), load(V3), load(V4)
order = list(base)
b = get_benchmark("gdpval"); b = b() if isinstance(b, type) else b
tasks = {t.id: t for t in b.load_tasks()}
# column registry: (key, label, dict)
COLS = [("base", "baseline (ACE)", base), ("pure", "pure+files", pure),
        ("v3", "FBL v3", v3), ("v4", "FBL v4", v4)]


def chars(d, t):
    return d[t].get("chars", d[t].get("metrics", {}).get("deliverable_chars", 0))


def brain_tokens(logs_dir, results_path):
    """Exact per-role tokens for the run that produced results_path. Window =
    [oldest of the newest-100 generator logs *at or before* the results mtime,
    results mtime]. Bounding by results mtime separates runs that share a log dir
    (e.g. FBL v3 vs v4)."""
    t_end = os.path.getmtime(results_path) + 5
    cand = [f for f in glob.glob(logs_dir + "/generator_*.json") if os.path.getmtime(f) <= t_end]
    gens = sorted(cand, key=os.path.getmtime)[-100:]
    t0 = os.path.getmtime(gens[0]) - 1
    agg = collections.defaultdict(lambda: [0, 0, 0])
    for f in glob.glob(logs_dir + "/*.json"):
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


def grader_tokens(results):
    """Reconstructed: 1 call/task. in=template+prompt+submission+rubric, out~=n_criteria. chars/4."""
    calls = pt = rt = 0
    for tid, r in results.items():
        t = tasks.get(tid)
        if not t:
            continue
        rub = (t.metadata or {}).get("rubric") or []
        rjson = json.dumps([{"rubric_item_id": c.get("rubric_item_id"), "score": c.get("score"),
                             "criterion": c.get("criterion"), "required": c.get("required")} for c in rub],
                            ensure_ascii=False)
        calls += 1
        pt += (len(G.GRADER_TEMPLATE) + len(t.prompt) + chars(results, tid) + len(rjson)) // 4
        rt += min(len(rub) * 60, 4096 * 4) // 4
    return calls, pt, rt


def pure_gen_tokens():
    calls = pt = rt = 0
    for tid in order:
        t = tasks.get(tid)
        if not t:
            continue
        files = reference_files_text(t.metadata)
        calls += 1
        pt += (len(PURE_SYS) + len(t.prompt) + len(files)) // 4
        rt += chars(pure, tid) // 4
    return calls, pt, rt


def stat(d):
    sc = [d[t]["score"] for t in order]
    return dict(mean=sum(sc)/len(sc), p=sum(s >= .5 for s in sc), g8=sum(s >= .8 for s in sc),
                z=sum(s == 0 for s in sc), dc=sum(chars(d, t) for t in order)//len(order))

P = print
P("# GDPval 4-way — baseline vs pure LLM+files vs FBL v3 vs FBL v4\n")
P("All four share the **same 100 gdpval train tasks, the same attachment file-I/O "
  "(xlsx/pdf/docx + image vision), the same Gemini `gemini-3.1-flash-lite` generator, and the "
  "same Gemini rubric grader** (via the rotation proxy). The only thing that changes is the "
  "**scaffolding around the generator**:\n")
P("- **baseline (ACE)** `ace_gdpval` — gen→reflect→curator, single playbook; only the JSON `final_answer` (asked *concise*) is graded.")
P("- **pure+files** — one raw LLM call, attachments injected, asked for the complete deliverable. No playbook, no JSON wrapper. (the ceiling)")
P("- **FBL v3** `ace_dual_gdpval` — dual playbook + Thompson + 2 curators; the playbook it grew contained a *harmful* mandatory \"I am unable to generate binary files…\" preamble bullet.")
P("- **FBL v4** — same, after editing the seeds/prompts to cut that disclaimer preamble.\n")

P("## 1. Overall scores\n")
P("| metric | baseline | pure+files | FBL v3 | FBL v4 |")
P("|---|---|---|---|---|")
st = {k: stat(d) for k, _, d in COLS}
best_mean = max(st[k]["mean"] for k, _, _ in COLS)
def mm(k): return (f"**{st[k]['mean']:.4f}** 🥇" if abs(st[k]['mean']-best_mean) < 1e-9 else f"{st[k]['mean']:.4f}")
P(f"| **mean score** | {mm('base')} | {mm('pure')} | {mm('v3')} | {mm('v4')} |")
P(f"| pass ≥0.5 | {st['base']['p']} | {st['pure']['p']} | {st['v3']['p']} | {st['v4']['p']} |")
P(f"| good ≥0.8 | {st['base']['g8']} | {st['pure']['g8']} | {st['v3']['g8']} | {st['v4']['g8']} |")
P(f"| zeros | {st['base']['z']} | {st['pure']['z']} | {st['v3']['z']} | {st['v4']['z']} |")
P(f"| avg deliverable chars | {st['base']['dc']} | {st['pure']['dc']} | {st['v3']['dc']} | {st['v4']['dc']} |")

def h2h(a, bb):
    aw = bw = tie = 0
    for t in order:
        x, y = a[t]["score"], bb[t]["score"]
        aw += x > y + 1e-9; bw += y > x + 1e-9; tie += abs(x-y) <= 1e-9
    return aw, bw, tie
w = h2h(v4, v3)
P(f"\n- **v4 vs v3**: v4 {w[0]} wins / v3 {w[1]} / tie {w[2]}  (the edit helped, net +)."
  f"  •  vs pure: v4 loses {h2h(pure, v4)[0]}, wins {h2h(pure, v4)[1]}, tie {h2h(pure, v4)[2]}.")
P(f"\n> Ranking: **pure {st['pure']['mean']:.3f} > v4 {st['v4']['mean']:.3f} > v3 {st['v3']['mean']:.3f} > baseline {st['base']['mean']:.3f}**. "
  f"Scaffolding still costs vs pure, but v4 narrows the gap to −{st['pure']['mean']-st['v4']['mean']:.3f} "
  f"(v3 was −{st['pure']['mean']-st['v3']['mean']:.3f}).\n")

P("## 2. By sector (mean score)\n")
P("| sector | n | baseline | pure+files | FBL v3 | FBL v4 |")
P("|---|---|---|---|---|---|")
sec = collections.defaultdict(lambda: {k: [] for k, _, _ in COLS})
for t in order:
    s = base[t]["metrics"].get("sector", "?")
    for k, _, d in COLS:
        sec[s][k].append(d[t]["score"])
for s in sorted(sec, key=lambda s: -sum(sec[s]["pure"])/len(sec[s]["pure"])):
    ms = {k: sum(sec[s][k])/len(sec[s][k]) for k, _, _ in COLS}
    win = max(ms.values())
    def tag(k): return f"**{ms[k]:.3f}**" if abs(ms[k]-win) < 1e-9 else f"{ms[k]:.3f}"
    P(f"| {s} | {len(sec[s]['pure'])} | {tag('base')} | {tag('pure')} | {tag('v3')} | {tag('v4')} |")

P("\n## 3. Token usage by role (calls + tokens)\n")
P("Brain roles = **exact** (`detailed_llm_logs`, windowed per run by completion time). "
  "`grader*` and pure's `llm-call*` = **reconstructed** (lengths → chars/4, Gemini-approx; not logged).\n")
bt = brain_tokens(BASE_LOGS, BASE)
t3 = brain_tokens(FBL_LOGS, V3)
t4 = brain_tokens(FBL_LOGS, V4)
def block(title, agg, gcalls, gpt, grt, extra=None):
    P(f"### {title}\n| role | calls | prompt tok | resp tok | total |")
    P("|---|---|---|---|---|")
    tc = tp = tr = 0
    if extra:
        c, p, r = extra
        P(f"| llm-call* | {c} | {p:,} | {r:,} | {p+r:,} |"); tc += c; tp += p; tr += r
    for role in ["generator", "reflector", "curator", "verifier"]:
        if role in agg:
            c, p, r = agg[role]
            P(f"| {role} | {c} | {p:,} | {r:,} | {p+r:,} |"); tc += c; tp += p; tr += r
    P(f"| grader* | {gcalls} | {gpt:,} | {grt:,} | {gpt+grt:,} |")
    P(f"| **TOTAL** | **{tc+gcalls}** | **{tp+gpt:,}** | **{tr+grt:,}** | **{tp+tr+gpt+grt:,}** |\n")
    return tc+gcalls, tp+tr+gpt+grt
tot = {}
tot["base"] = block("baseline (ACE)", bt, *grader_tokens(base))
tot["pure"] = block("pure+files", {}, *grader_tokens(pure), extra=pure_gen_tokens())
tot["v3"] = block("FBL v3", t3, *grader_tokens(v3))
tot["v4"] = block("FBL v4", t4, *grader_tokens(v4))
P("### Token summary\n| | baseline | pure+files | FBL v3 | FBL v4 |")
P("|---|---|---|---|---|")
P(f"| total calls (incl. grader) | {tot['base'][0]} | {tot['pure'][0]} | {tot['v3'][0]} | {tot['v4'][0]} |")
P(f"| total tokens (incl. grader) | {tot['base'][1]:,} | {tot['pure'][1]:,} | {tot['v3'][1]:,} | {tot['v4'][1]:,} |")
P("\n_pure is by far the cheapest (1 gen + 1 grader/task) and the highest-scoring; the FBL variants "
  "spend the most (dual-playbook reflect/curate) for a still-lower score._\n")

P("## 4. All 100 tasks — score line-up (dataset order)\n")
P("Winner **bolded**. `Δv4−v3` shows the effect of the v4 edit per task.\n")
P("| # | task_id | sector | baseline | pure+files | FBL v3 | FBL v4 | Δv4−v3 | best |")
P("|---|---|---|---|---|---|---|---|---|")
for i, t in enumerate(order, 1):
    vals = {k: d[t]["score"] for k, _, d in COLS}
    win = max(vals.values())
    def tg(k): return f"**{vals[k]:.3f}**" if abs(vals[k]-win) < 1e-9 else f"{vals[k]:.3f}"
    who = next(lbl.split()[0].replace("+files", "") for k, lbl, _ in COLS if abs(vals[k]-win) < 1e-9)
    dv = vals["v4"] - vals["v3"]
    sct = base[t]["metrics"].get("sector", "?")[:18]
    P(f"| {i} | {t[:8]} | {sct} | {tg('base')} | {tg('pure')} | {tg('v3')} | {tg('v4')} | {dv:+.3f} | {who} |")

bestc = {k: sum(d[t]["score"] >= max(x[t]["score"] for _, _, x in COLS) - 1e-9 for t in order) for k, _, d in COLS}
P(f"\n_(tied-)best count: pure {bestc['pure']}, v4 {bestc['v4']}, v3 {bestc['v3']}, baseline {bestc['base']} out of 100._")
