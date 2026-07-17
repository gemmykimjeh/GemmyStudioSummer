"""3-way GDPval report: baseline ACE vs pure LLM+tools vs FBL v5.

Same 100 gdpval train tasks, same file-I/O + codegen pipeline, same Gemini
generator + robust grader (rotation proxy). Only the scaffold around the
generator changes.
  - baseline   `ace_gdpval`     — single playbook, gen->reflect->curator (UNCHANGED)
  - pure+tools — one raw LLM call + the same codegen/file pipeline, no scaffold
  - FBL v5     `ace_dual_gdpval`— single-playbook ACE + immutable rulebook +
                 ①source manifest ②read-source rule +execute-and-repair loop
                 +independent-path verify (rb-chk-01)

Structure mirrors AB_cold_5way.md (overall / by sector / token-by-role /
all-100 lineup) but every number is recomputed from the CURRENT runs.
Usage: python scripts/compare_3way.py > AB_v5_3way.md
"""
import glob, json, os, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
import harness.benchmarks.gdpval as G                      # noqa: E402
from harness.registry import get_benchmark                  # noqa: E402
from harness.benchmarks.gdpval_files import reference_files_text  # noqa: E402

SP = (r"C:\Users\wooja\AppData\Local\Temp\claude\C--GemmyStudioSummer"
      r"\83c0eccb-4e41-4331-9ab7-755da18bf404\scratchpad")
PURE = os.path.join(SP, "pure100_tools.jsonl")
BASE = "runs/baseline_ace_100/gdpval__ace_gdpval/results.jsonl"
V5   = "runs/gemini_fbl_v5_final/gdpval__ace_dual_gdpval/results.jsonl"
BASE_LOGS = "ace_gdpval_run/detailed_llm_logs"
FBL_LOGS  = "ace_dual_gdpval_run/detailed_llm_logs"
# pure+tools system prompt (for token reconstruction only)
PURE_SYS = ("You are completing a real professional work task. If the deliverable is an office "
            "file, output ONE python code block that builds and saves it; read source files by "
            "exact name. Otherwise write the content as text. Be thorough; graded on a rubric.")


def load_jsonl_map(p):
    out = {}
    for l in open(p, encoding="utf-8"):
        d = json.loads(l)
        out[d["task_id"]] = d
    return out


base, pure, v5 = load_jsonl_map(BASE), load_jsonl_map(PURE), load_jsonl_map(V5)
order = [t for t in base if t in v5 and t in pure]  # common set, dataset order
b = get_benchmark("gdpval"); b = b() if isinstance(b, type) else b
tasks = {t.id: t for t in b.load_tasks()}
COLS = [("base", "baseline", base), ("pure", "pure+tools", pure), ("v5", "FBL v5", v5)]


def score(d, t):
    return d[t].get("score", 0.0)


def chars(d, t):
    return d[t].get("chars", (d[t].get("metrics") or {}).get("deliverable_chars", 0))


def sector(t):
    return (base[t].get("metrics") or {}).get("sector", "?")


def brain_tokens(logs_dir, results_path):
    """Exact generator/reflector/curator tokens, windowed to the last 100 gens
    completed at/before this run's results mtime."""
    t_end = os.path.getmtime(results_path) + 5
    cand = [f for f in glob.glob(logs_dir + "/generator_*.json") if os.path.getmtime(f) <= t_end]
    gens = sorted(cand, key=os.path.getmtime)[-100:]
    if not gens:
        return {}
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
    for tid in order:
        t = tasks.get(tid)
        if not t:
            continue
        rub = (t.metadata or {}).get("rubric") or []
        rjson = json.dumps([{"rubric_item_id": c.get("rubric_item_id"), "score": c.get("score"),
                             "criterion": c.get("criterion"), "required": c.get("required")}
                            for c in rub], ensure_ascii=False)
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
    sc = [score(d, t) for t in order]
    return dict(mean=sum(sc)/len(sc), p=sum(s >= .5 for s in sc), g8=sum(s >= .8 for s in sc),
                perf=sum(s >= .999 for s in sc), z=sum(s == 0 for s in sc),
                dc=sum(chars(d, t) for t in order)//len(order))


st = {k: stat(d) for k, _, d in COLS}
P = print
P("# GDPval 3-way — baseline ACE vs pure LLM+tools vs FBL v5\n")
P(f"Same {len(order)} gdpval train tasks, same attachment file-I/O + codegen pipeline, same "
  "Gemini generator + robust grader (rotation proxy, temperature-0 + retry). Only the "
  "scaffold around the generator changes.\n")
P("- **baseline** `ace_gdpval` — single-playbook ACE, gen→reflect→curator. **Untouched** (control).")
P("- **pure+tools** — one raw LLM call + the same file/codegen pipeline, no ACE scaffold. (the ceiling reference)")
P("- **FBL v5** `ace_dual_gdpval` — single-playbook ACE + immutable rulebook, plus this cycle's "
  "changes: ①source-file manifest, ②read-source-not-inline rule (rb-fmt-08), execute-and-repair "
  "loop (run code → feed traceback+schema → fix, ≤2×), independent-path self-verify (rb-chk-01).\n")

P("## 1. Overall scores\n")
P("| metric | baseline | pure+tools | FBL v5 |")
P("|---|---|---|---|")
bestmean = max(st[k]["mean"] for k, _, _ in COLS)
def mm(k): return (f"**{st[k]['mean']:.4f}** 🥇" if abs(st[k]['mean']-bestmean) < 1e-9 else f"{st[k]['mean']:.4f}")
P(f"| **mean score** | {mm('base')} | {mm('pure')} | {mm('v5')} |")
bestp = max(st[k]["p"] for k, _, _ in COLS)
def pp(k): return (f"**{st[k]['p']}** 🥇" if st[k]['p'] == bestp else f"{st[k]['p']}")
P(f"| pass ≥0.5 | {pp('base')} | {pp('pure')} | {pp('v5')} |")
P(f"| good ≥0.8 | {st['base']['g8']} | {st['pure']['g8']} | {st['v5']['g8']} |")
P(f"| perfect 1.0 | {st['base']['perf']} | {st['pure']['perf']} | {st['v5']['perf']} |")
P(f"| zeros | {st['base']['z']} | {st['pure']['z']} | {st['v5']['z']} |")
P(f"| avg deliverable chars | {st['base']['dc']} | {st['pure']['dc']} | {st['v5']['dc']} |")


def h2h(a, bb):
    aw = bw = tie = 0
    for t in order:
        x, y = score(a, t), score(bb, t)
        aw += x > y + 1e-9; bw += y > x + 1e-9; tie += abs(x-y) <= 1e-9
    return aw, bw, tie


P(f"\nHead-to-head: FBL v5 vs baseline {h2h(v5, base)[0]}–{h2h(v5, base)[1]} (tie {h2h(v5, base)[2]}), "
  f"FBL v5 vs pure {h2h(v5, pure)[0]}–{h2h(v5, pure)[1]} (tie {h2h(v5, pure)[2]}).\n")

P("## 2. By sector (mean score)\n")
P("| sector | n | baseline | pure+tools | FBL v5 |")
P("|---|---|---|---|---|")
sec = collections.defaultdict(lambda: {k: [] for k, _, _ in COLS})
for t in order:
    s = sector(t)
    for k, _, d in COLS:
        sec[s][k].append(score(d, t))
for s in sorted(sec, key=lambda s: -sum(sec[s]["v5"])/len(sec[s]["v5"])):
    ms = {k: sum(sec[s][k])/len(sec[s][k]) for k, _, _ in COLS}
    win = max(ms.values())
    def tag(k): return f"**{ms[k]:.3f}**" if abs(ms[k]-win) < 1e-9 else f"{ms[k]:.3f}"
    P(f"| {s} | {len(sec[s]['v5'])} | {tag('base')} | {tag('pure')} | {tag('v5')} |")

P("\n## 3. Token usage by role (calls + tokens)\n")
P("Brain roles (generator/reflector/curator) = **exact** (`detailed_llm_logs`, windowed per run "
  "by completion time). `grader*` and pure's `llm-call*` = **reconstructed** (lengths → chars/4, "
  "Gemini-approx; not logged).\n")
tok = {"base": brain_tokens(BASE_LOGS, BASE), "v5": brain_tokens(FBL_LOGS, V5)}


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
tt["pure"] = block("pure+tools", {}, *grader_tokens(pure), extra=pure_gen_tokens())
tt["v5"] = block("FBL v5", tok["v5"], *grader_tokens(v5))
P("### Token summary\n| | baseline | pure+tools | FBL v5 |")
P("|---|---|---|---|")
P(f"| total calls | {tt['base'][0]} | {tt['pure'][0]} | {tt['v5'][0]} |")
P(f"| total tokens | {tt['base'][1]:,} | {tt['pure'][1]:,} | {tt['v5'][1]:,} |")

P("\n## 4. All 100 tasks — score line-up (dataset order)\n")
P("Winner **bolded**. `Δv5−base` = FBL v5's effect over the untouched baseline per task.\n")
P("| # | task_id | sector | gold | baseline | pure | FBL v5 | Δv5−base | best |")
P("|---|---|---|---|---|---|---|---|---|")
for i, t in enumerate(order, 1):
    vals = {k: score(d, t) for k, _, d in COLS}
    win = max(vals.values())
    def tg(k): return f"**{vals[k]:.3f}**" if abs(vals[k]-win) < 1e-9 else f"{vals[k]:.3f}"
    who = next(lbl.replace("+tools", "").replace("FBL ", "") for k, lbl, _ in COLS if abs(vals[k]-win) < 1e-9)
    gt = ",".join(g.lstrip(".") for g in ((v5[t].get("metrics") or {}).get("gold_types") or [])) or "-"
    P(f"| {i} | {t[:8]} | {sector(t)[:14]} | {gt} | {tg('base')} | {tg('pure')} | {tg('v5')} "
      f"| {vals['v5']-vals['base']:+.3f} | {who} |")

bestc = {k: sum(score(d, t) >= max(score(x, t) for _, _, x in COLS) - 1e-9 for t in order)
         for k, _, d in COLS}
P(f"\n_(tied-)best count: pure {bestc['pure']}, FBL v5 {bestc['v5']}, baseline {bestc['base']} / {len(order)}._")
