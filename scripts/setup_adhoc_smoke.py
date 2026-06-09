"""Select 30 random ad-hoc questions (5 per source) and create task directories."""
import json
import random
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADHOC_ROOT = REPO_ROOT / "results" / "ad_hoc_repo_eval"

SOURCES = {
    "gemini-cli": {
        "questions": ADHOC_ROOT / "tasks" / "gemini-cli-agent-tooling" / "questions.json",
        "docs": ADHOC_ROOT / "tasks" / "gemini-cli-agent-tooling" / "source_documents",
    },
    "mapu": {
        "questions": ADHOC_ROOT / "tasks" / "mapu-recall-fix-memory-control-plane" / "questions.json",
        "docs": ADHOC_ROOT / "tasks" / "mapu-recall-fix-memory-control-plane" / "source_documents",
    },
    "latent-space": {
        "questions": ADHOC_ROOT / "tasks" / "latent-space-reasoning-spend-gate" / "questions.json",
        "docs": ADHOC_ROOT / "tasks" / "latent-space-reasoning-spend-gate" / "source_documents",
    },
    "animations": {
        "questions": ADHOC_ROOT / "questions.json",
        "docs": Path(r"C:\Users\devan\Downloads\PXiiNtVNTBGjzcCw8-4nnA\animations\documents\converted\pdf"),
    },
    "datadog": {
        "questions": Path(r"C:\Users\devan\OneDrive\Desktop\Projects\Data Dog\questions.json"),
        "docs": Path(r"C:\Users\devan\OneDrive\Desktop\Projects\Data Dog"),
    },
    "servicenow": {
        "questions": Path(r"C:\Users\devan\OneDrive\Desktop\Projects\ServiceNow\questions.json"),
        "docs": Path(r"C:\Users\devan\OneDrive\Desktop\Projects\ServiceNow"),
    },
}


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else random.randint(0, 2**32 - 1)
    rng = random.Random(seed)
    out_root = sys.argv[2] if len(sys.argv) > 2 else str(REPO_ROOT / "results" / "smoke_v3" / "ad_hoc")

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    all_selected = []
    tasks_root = out_root / "tasks"
    tasks_root.mkdir(parents=True, exist_ok=True)

    for source_key, cfg in SOURCES.items():
        qfile = cfg["questions"]
        if not qfile.exists():
            print(f"SKIP {source_key}: {qfile} not found")
            continue

        questions = json.loads(qfile.read_text(encoding="utf-8-sig"))
        selected = rng.sample(questions, min(5, len(questions)))
        print(f"{source_key}: selected {len(selected)} of {len(questions)}")

        for q in selected:
            qid = q.get("id", "unknown")
            task_id = f"{source_key}/q{qid}"
            task_dir = tasks_root / source_key / f"q{qid}"
            task_dir.mkdir(parents=True, exist_ok=True)

            task_json = {
                "title": f"{source_key} — Question {qid}",
                "instructions": q["question"],
                "deliverables": {"report": "answer.md"},
                "metadata": {
                    "source": source_key,
                    "question_id": qid,
                    "type": q.get("type", "unknown"),
                    "difficulty": q.get("difficulty", "unknown"),
                    "cross_domain_angle": q.get("cross_domain_angle"),
                    "requires_documents": q.get("requires_documents", []),
                },
            }

            docs_dir = cfg["docs"]
            if source_key in ("gemini-cli", "mapu", "latent-space"):
                src_docs = task_dir / "source_documents"
                if src_docs.exists():
                    shutil.rmtree(src_docs)
                shutil.copytree(docs_dir, src_docs)
            else:
                task_json["docs_path"] = str(docs_dir)

            (task_dir / "task.json").write_text(
                json.dumps(task_json, indent=2), encoding="utf-8"
            )

            all_selected.append({
                "task_id": task_id,
                "source": source_key,
                "question_id": qid,
            })

    manifest = {
        "sources": [
            {
                "name": "ad_hoc",
                "type": "local",
                "root": str(out_root),
                "default_scorer": "llm_judge",
            }
        ],
        "seed": seed,
        "per_source": 5,
        "task_count": len(all_selected),
        "source_count": len(SOURCES),
        "tasks": [{"task_id": t["task_id"], "source": "ad_hoc"} for t in all_selected],
    }
    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nManifest: {manifest_path}")
    print(f"Total: {len(all_selected)} tasks from {len(SOURCES)} sources (seed={seed})")
    return manifest_path

if __name__ == "__main__":
    main()
