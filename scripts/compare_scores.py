"""Compare scored results between two runs."""
import json
import pathlib
import sys

def load_scores(d):
    scores = {}
    for sf in pathlib.Path(d).rglob("scores.json"):
        data = json.loads(sf.read_text())
        task_parts = sf.relative_to(d).parent.parts
        task = "/".join(task_parts)
        n_passed = data.get("n_passed", 0)
        n_criteria = data.get("n_criteria", 0)
        all_pass = data.get("all_pass", False)
        scores[task] = (n_passed, n_criteria, all_pass)
    return scores

base_dir = sys.argv[1]
fix_dir = sys.argv[2]

base = load_scores(base_dir)
fix = load_scores(fix_dir)

print(f"Baseline tasks: {len(base)}, Fix tasks: {len(fix)}")
print()

header = f"{'Task':<65} {'Base':>8} {'Fix':>8} {'Delta':>8}"
print(header)
print("-" * 95)

all_tasks = sorted(set(base) | set(fix))
total_delta = 0
n = 0
improved = 0
regressed = 0

for t in all_tasks:
    bp, bt, ba = base.get(t, (0, 0, False))
    fp, ft, fa = fix.get(t, (0, 0, False))
    if bt > 0 and ft > 0:
        bpct = bp / bt * 100
        fpct = fp / ft * 100
        delta = fpct - bpct
        total_delta += delta
        n += 1
        if delta > 2:
            improved += 1
        if delta < -2:
            regressed += 1
        b_ap = "P" if ba else "."
        f_ap = "P" if fa else "."
        marker = "+++" if delta > 5 else ("++" if delta > 2 else ("--" if delta < -5 else ("-" if delta < -2 else "")))
        print(f"{t[:64]:<65} {bp:>2}/{bt:<2} {bpct:>5.1f}% {b_ap}  {fp:>2}/{ft:<2} {fpct:>5.1f}% {f_ap} {delta:>+6.1f} {marker}")
    elif bt > 0:
        bpct = bp / bt * 100
        print(f"{t[:64]:<65} {bp:>2}/{bt:<2} {bpct:>5.1f}%    MISSING")
    elif ft > 0:
        fpct = fp / ft * 100
        print(f"{t[:64]:<65}    MISSING       {fp:>2}/{ft:<2} {fpct:>5.1f}%")

print()
print(f"Avg delta: {total_delta/max(n,1):+.1f}pp across {n} common tasks")
print(f"Improved (>2pp): {improved}, Regressed (<-2pp): {regressed}")
