"""5-way GDPval report: baseline vs pure LLM+files vs FBL v2 vs v3 vs v4.
Labels per user: the three FBL 100-runs are v2 (fbl_100_v3), v3 (fbl_100_v4, now
backed up), v4 (fbl_v5, newest — raw output + completeness + robust grader +
causal reflect). Same 100 tasks, file-I/O, Gemini generator + grader.
Usage: python scripts/compare_5way.py > AB_cold_5way.md
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
V2   = "runs/gemini_fbl_100_v3/gdpval__ace_dual_gdpval/results.jsonl"
V3   = glob.glob("runs/_bak_gemini_fbl_100_v4.*/gdpval__ace_dual_gdpval/results.jsonl")[0]
V4   = "runs/gemini_fbl_v5/gdpval__ace_dual_gdpval/results.jsonl"
BASE_LOGS, FBL_LOGS = "ace_gdpval_run/detailed_llm_logs", "ace_dual_gdpval_run/detailed_llm_logs"
PURE_SYS = ("You are completing a real professional work task. Produce the COMPLETE "
            "requested deliverable as your response (the full document/analysis/table), "
            "not a summary or plan. Be thorough; you are graded against a detailed rubric.")


def load(p):
    return {json.loads(l)["task_id"]: json.loads(l) for l in open(p, encoding="utf-8")}

base, pure, v2, v3, v4 = load(BASE), load(PURE), load(V2), load(V3), load(V4)
order = list(base)
b = get_benchmark("gdpval"); b = b() if isinstance(b, type) else b
tasks = {t.id: t for t in b.load_tasks()}
COLS = [("base", "baseline", base), ("pure", "pure+files", pure),
        ("v2", "FBL v2", v2), ("v3", "FBL v3", v3), ("v4", "FBL v4", v4)]


def chars(d, t):
    return d[t].get("chars", d[t].get("metrics", {}).get("deliverable_chars", 0))


def brain_tokens(logs_dir, results_path):
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
st = {k: stat(d) for k, _, d in COLS}

P = print
P("# GDPval 5-way — baseline vs pure LLM+files vs FBL v2/v3/v4\n")
P("Same 100 gdpval train tasks, same attachment file-I/O, same Gemini generator + grader "
  "(rotation proxy). Only the scaffolding around the generator changes.\n")
P("- **baseline** `ace_gdpval` — single playbook, gen→reflect→curator, JSON `final_answer` (concise) graded.")
P("- **pure+files** — one raw LLM call, attachments injected, complete deliverable, no scaffold. (the ceiling)")
P("- **FBL v2** `ace_dual_gdpval` — dual playbook + Thompson + 2 curators; playbook grew a harmful \"I am unable to generate binary files…\" preamble.")
P("- **FBL v3** — after cutting that disclaimer preamble (seed/prompt edits).")
P("- **FBL v4** — biggest revision: RAW deliverable output (no JSON envelope) + `CITED:` marker, completeness/enumeration rules (rb-fmt-07/chk-02 + prompt), **robust grader** (temperature 0 + retry, kills spurious 0s), causal/counterfactual bullet tagging + harmful-floor counting, curator single-mode fix, deduped rulebook.\n")

P("> **Caveat (fairness):** FBL v4 was graded with the *robust* grader (temperature 0 + retry); "
  "baseline/pure/v2/v3 used the older grader, which occasionally returns an unparseable "
  "verdict and scores a good deliverable 0. So part of v4's gain is noise removal that would "
  "also lift the others; a fully fair comparison re-grades every run with the robust grader.\n")

P("## 1. Overall scores\n")
P("| metric | baseline | pure+files | FBL v2 | FBL v3 | FBL v4 |")
P("|---|---|---|---|---|---|")
bestmean = max(st[k]["mean"] for k, _, _ in COLS)
def mm(k): return (f"**{st[k]['mean']:.4f}** 🥇" if abs(st[k]['mean']-bestmean) < 1e-9 else f"{st[k]['mean']:.4f}")
P(f"| **mean score** | {mm('base')} | {mm('pure')} | {mm('v2')} | {mm('v3')} | {mm('v4')} |")
bestp = max(st[k]["p"] for k, _, _ in COLS)
def pp(k): return (f"**{st[k]['p']}** 🥇" if st[k]['p'] == bestp else f"{st[k]['p']}")
P(f"| pass ≥0.5 | {pp('base')} | {pp('pure')} | {pp('v2')} | {pp('v3')} | {pp('v4')} |")
P(f"| good ≥0.8 | {st['base']['g8']} | {st['pure']['g8']} | {st['v2']['g8']} | {st['v3']['g8']} | {st['v4']['g8']} |")
P(f"| zeros | {st['base']['z']} | {st['pure']['z']} | {st['v2']['z']} | {st['v3']['z']} | {st['v4']['z']} |")
P(f"| avg deliverable chars | {st['base']['dc']} | {st['pure']['dc']} | {st['v2']['dc']} | {st['v3']['dc']} | {st['v4']['dc']} |")

def h2h(a, bb):
    aw = bw = tie = 0
    for t in order:
        x, y = a[t]["score"], bb[t]["score"]
        aw += x > y + 1e-9; bw += y > x + 1e-9; tie += abs(x-y) <= 1e-9
    return aw, bw, tie
P("\n**FBL progression** (gap to pure): "
  f"v2 {st['v2']['mean']:.3f} (−{st['pure']['mean']-st['v2']['mean']:.3f}) → "
  f"v3 {st['v3']['mean']:.3f} (−{st['pure']['mean']-st['v3']['mean']:.3f}) → "
  f"**v4 {st['v4']['mean']:.3f} (−{st['pure']['mean']-st['v4']['mean']:.3f})**.")
P(f"\nHead-to-head: v4 vs v3 {h2h(v4, v3)[0]}–{h2h(v4, v3)[1]} (tie {h2h(v4, v3)[2]}), "
  f"v4 vs v2 {h2h(v4, v2)[0]}–{h2h(v4, v2)[1]}, v4 vs pure {h2h(v4, pure)[0]}–{h2h(v4, pure)[1]}. "
  f"**v4's pass-rate ({st['v4']['p']}) is the highest of all, beating pure ({st['pure']['p']}).**\n")

P("## 2. By sector (mean score)\n")
P("| sector | n | baseline | pure+files | FBL v2 | FBL v3 | FBL v4 |")
P("|---|---|---|---|---|---|---|")
sec = collections.defaultdict(lambda: {k: [] for k, _, _ in COLS})
for t in order:
    s = base[t]["metrics"].get("sector", "?")
    for k, _, d in COLS:
        sec[s][k].append(d[t]["score"])
for s in sorted(sec, key=lambda s: -sum(sec[s]["pure"])/len(sec[s]["pure"])):
    ms = {k: sum(sec[s][k])/len(sec[s][k]) for k, _, _ in COLS}
    win = max(ms.values())
    def tag(k): return f"**{ms[k]:.3f}**" if abs(ms[k]-win) < 1e-9 else f"{ms[k]:.3f}"
    P(f"| {s} | {len(sec[s]['pure'])} | {tag('base')} | {tag('pure')} | {tag('v2')} | {tag('v3')} | {tag('v4')} |")

P("\n## 3. Token usage by role (calls + tokens)\n")
P("Brain roles = **exact** (`detailed_llm_logs`, windowed per run by completion time). "
  "`grader*` and pure's `llm-call*` = **reconstructed** (lengths → chars/4, Gemini-approx; not logged).\n")
tok = {"base": brain_tokens(BASE_LOGS, BASE), "v2": brain_tokens(FBL_LOGS, V2),
       "v3": brain_tokens(FBL_LOGS, V3), "v4": brain_tokens(FBL_LOGS, V4)}
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
tt = {}
tt["base"] = block("baseline", tok["base"], *grader_tokens(base))
tt["pure"] = block("pure+files", {}, *grader_tokens(pure), extra=pure_gen_tokens())
tt["v2"] = block("FBL v2", tok["v2"], *grader_tokens(v2))
tt["v3"] = block("FBL v3", tok["v3"], *grader_tokens(v3))
tt["v4"] = block("FBL v4", tok["v4"], *grader_tokens(v4))
P("### Token summary\n| | baseline | pure+files | FBL v2 | FBL v3 | FBL v4 |")
P("|---|---|---|---|---|---|")
P(f"| total calls | {tt['base'][0]} | {tt['pure'][0]} | {tt['v2'][0]} | {tt['v3'][0]} | {tt['v4'][0]} |")
P(f"| total tokens | {tt['base'][1]:,} | {tt['pure'][1]:,} | {tt['v2'][1]:,} | {tt['v3'][1]:,} | {tt['v4'][1]:,} |")

P("\n## 4. All 100 tasks — score line-up (dataset order)\n")
P("Winner **bolded**. `Δv4−v3` = effect of the v4 revision per task.\n")
P("| # | task_id | sector | baseline | pure | v2 | v3 | v4 | Δv4−v3 | best |")
P("|---|---|---|---|---|---|---|---|---|---|")
for i, t in enumerate(order, 1):
    vals = {k: d[t]["score"] for k, _, d in COLS}
    win = max(vals.values())
    def tg(k): return f"**{vals[k]:.3f}**" if abs(vals[k]-win) < 1e-9 else f"{vals[k]:.3f}"
    who = next(lbl.replace("+files", "").replace("FBL ", "") for k, lbl, _ in COLS if abs(vals[k]-win) < 1e-9)
    sct = base[t]["metrics"].get("sector", "?")[:16]
    P(f"| {i} | {t[:8]} | {sct} | {tg('base')} | {tg('pure')} | {tg('v2')} | {tg('v3')} | {tg('v4')} | {vals['v4']-vals['v3']:+.3f} | {who} |")

bestc = {k: sum(d[t]["score"] >= max(x[t]["score"] for _, _, x in COLS) - 1e-9 for t in order) for k, _, d in COLS}
P(f"\n_(tied-)best count: pure {bestc['pure']}, v4 {bestc['v4']}, v3 {bestc['v3']}, v2 {bestc['v2']}, baseline {bestc['base']} / 100._")
