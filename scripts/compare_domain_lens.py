"""Compare domain lens (exp-003) vs baseline-match (exp-001) scores.

Evaluates against go gate:
  - 82%+ criteria rate
  - 3/48+ all-pass
  - Bottom-10 avg +6pp
  - Top-10 regress <1.5pp
"""
import json
import sys
from pathlib import Path

BASELINE_DIR = Path("results/phase0_baseline_match")
DOMLENS_DIR = Path("results/phase0_domain_lens")


def _normalize_task_key(rel_path: str) -> str:
    """Strip scenario-XX level to get canonical category/task key."""
    import re
    parts = rel_path.replace("\\", "/").split("/")
    parts = [p for p in parts if not re.match(r"^scenario-\d+$", p)]
    return "/".join(parts)


def load_scores(results_dir: Path) -> dict[str, dict]:
    scores = {}
    for sf in results_dir.rglob("scores.json"):
        task_dir = sf.parent
        rel = task_dir.relative_to(results_dir)
        task_key = _normalize_task_key(str(rel))

        data = json.loads(sf.read_text(encoding="utf-8"))
        n_criteria = data.get("n_criteria", 0)
        n_passed = data.get("n_passed", 0)
        rate = round(100 * n_passed / n_criteria, 1) if n_criteria > 0 else 0
        scores[task_key] = {
            "n_criteria": n_criteria,
            "n_passed": n_passed,
            "rate": rate,
            "all_pass": data.get("all_pass", False),
        }
    return scores


def main():
    bl = load_scores(BASELINE_DIR)
    dl = load_scores(DOMLENS_DIR)

    if not dl:
        print("ERROR: No domain lens scores found.")
        sys.exit(1)

    print(f"Baseline tasks scored: {len(bl)}")
    print(f"Domain lens tasks scored: {len(dl)}")
    print()

    common = sorted(set(bl.keys()) & set(dl.keys()))
    if not common:
        print("ERROR: No common tasks between baseline and domain lens.")
        sys.exit(1)

    rows = []
    for task in common:
        delta = dl[task]["rate"] - bl[task]["rate"]
        rows.append({
            "task": task,
            "bl_rate": bl[task]["rate"],
            "dl_rate": dl[task]["rate"],
            "delta": delta,
            "bl_pass": bl[task]["n_passed"],
            "bl_total": bl[task]["n_criteria"],
            "dl_pass": dl[task]["n_passed"],
            "dl_total": dl[task]["n_criteria"],
            "bl_all": bl[task]["all_pass"],
            "dl_all": dl[task]["all_pass"],
        })

    rows.sort(key=lambda r: r["bl_rate"])

    # Overall metrics
    bl_avg = sum(r["bl_rate"] for r in rows) / len(rows)
    dl_avg = sum(r["dl_rate"] for r in rows) / len(rows)
    bl_all_pass = sum(1 for r in rows if r["bl_all"])
    dl_all_pass = sum(1 for r in rows if r["dl_all"])

    # Bottom 10 / Top 10 analysis
    bottom_10 = rows[:10]
    top_10 = rows[-10:]
    bl_bottom_avg = sum(r["bl_rate"] for r in bottom_10) / 10
    dl_bottom_avg = sum(r["dl_rate"] for r in bottom_10) / 10
    bl_top_avg = sum(r["bl_rate"] for r in top_10) / 10
    dl_top_avg = sum(r["dl_rate"] for r in top_10) / 10

    # Go gate evaluation
    print("=" * 70)
    print("GO GATE EVALUATION (exp-003 Domain Lens)")
    print("=" * 70)
    gate_pass = True

    g1 = dl_avg >= 82.0
    print(f"  1. Criteria rate >= 82%:     {dl_avg:.1f}% {'PASS' if g1 else 'FAIL'}")
    gate_pass &= g1

    g2 = dl_all_pass >= 3
    print(f"  2. All-pass >= 3/48:         {dl_all_pass}/48 {'PASS' if g2 else 'FAIL'}")
    gate_pass &= g2

    bottom_delta = dl_bottom_avg - bl_bottom_avg
    g3 = bottom_delta >= 6.0
    print(f"  3. Bottom-10 avg +6pp:       {bottom_delta:+.1f}pp (BL={bl_bottom_avg:.1f}% -> DL={dl_bottom_avg:.1f}%) {'PASS' if g3 else 'FAIL'}")
    gate_pass &= g3

    top_regress = bl_top_avg - dl_top_avg
    g4 = top_regress < 1.5
    print(f"  4. Top-10 regress <1.5pp:    {top_regress:+.1f}pp (BL={bl_top_avg:.1f}% -> DL={dl_top_avg:.1f}%) {'PASS' if g4 else 'FAIL'}")
    gate_pass &= g4

    print()
    print(f"  OVERALL: {'>>> GATE PASSES <<<' if gate_pass else '*** GATE FAILS ***'}")
    print()

    # Summary table
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Criteria rate:  {bl_avg:.1f}% -> {dl_avg:.1f}% ({dl_avg - bl_avg:+.1f}pp)")
    print(f"  All-pass:       {bl_all_pass}/48 -> {dl_all_pass}/48")
    print(f"  Bottom-10 avg:  {bl_bottom_avg:.1f}% -> {dl_bottom_avg:.1f}% ({bottom_delta:+.1f}pp)")
    print(f"  Top-10 avg:     {bl_top_avg:.1f}% -> {dl_top_avg:.1f}% ({dl_top_avg - bl_top_avg:+.1f}pp)")
    print()

    # Per-task delta table
    print("=" * 70)
    print("PER-TASK DELTAS (sorted by baseline rate)")
    print("=" * 70)
    print(f"  {'Task':<65} {'BL%':>5} {'DL%':>5} {'Delta':>6} {'AP':>3}")
    print("  " + "-" * 84)

    improved = 0
    regressed = 0
    unchanged = 0

    for r in rows:
        short = r["task"]
        if len(short) > 64:
            short = short[:61] + "..."
        ap = "*" if r["dl_all"] else ""
        d = f"{r['delta']:+.1f}"
        marker = ""
        if r["delta"] > 0:
            improved += 1
        elif r["delta"] < 0:
            regressed += 1
            marker = " <-REGRESS"
        else:
            unchanged += 1
        print(f"  {short:<65} {r['bl_rate']:5.1f} {r['dl_rate']:5.1f} {d:>6} {ap:>3}{marker}")

    print()
    print(f"  Improved: {improved}, Regressed: {regressed}, Unchanged: {unchanged}")

    # Category breakdown
    print()
    print("=" * 70)
    print("CATEGORY BREAKDOWN")
    print("=" * 70)
    cats = {}
    for r in rows:
        cat = r["task"].split("/")[0]
        if cat not in cats:
            cats[cat] = {"bl": [], "dl": []}
        cats[cat]["bl"].append(r["bl_rate"])
        cats[cat]["dl"].append(r["dl_rate"])

    print(f"  {'Category':<50} {'BL%':>5} {'DL%':>5} {'Delta':>6}")
    print("  " + "-" * 66)
    for cat in sorted(cats.keys()):
        bl_cat = sum(cats[cat]["bl"]) / len(cats[cat]["bl"])
        dl_cat = sum(cats[cat]["dl"]) / len(cats[cat]["dl"])
        delta = dl_cat - bl_cat
        print(f"  {cat:<50} {bl_cat:5.1f} {dl_cat:5.1f} {delta:+6.1f}")

    # New all-pass tasks
    new_ap = [r for r in rows if r["dl_all"] and not r["bl_all"]]
    if new_ap:
        print()
        print("NEW ALL-PASS TASKS:")
        for r in new_ap:
            print(f"  {r['task']} ({r['dl_rate']:.1f}%)")

    lost_ap = [r for r in rows if r["bl_all"] and not r["dl_all"]]
    if lost_ap:
        print()
        print("LOST ALL-PASS TASKS:")
        for r in lost_ap:
            print(f"  {r['task']} ({r['bl_rate']:.1f}% -> {r['dl_rate']:.1f}%)")

    # Write JSON summary for experiment ledger
    summary = {
        "criteria_rate": round(dl_avg, 1),
        "all_pass_rate": round(100 * dl_all_pass / len(common), 1),
        "all_pass_count": dl_all_pass,
        "total_tasks": len(common),
        "vs_baseline_delta_pp": round(dl_avg - bl_avg, 1),
        "improved_tasks": improved,
        "regressed_tasks": regressed,
        "bottom_10_delta_pp": round(bottom_delta, 1),
        "top_10_regress_pp": round(top_regress, 1),
        "gate_pass": gate_pass,
    }
    out = Path(".review_tmp/domain_lens_comparison.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nSummary written to {out}")


if __name__ == "__main__":
    main()
