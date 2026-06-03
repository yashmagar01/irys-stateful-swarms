from __future__ import annotations

import json
from pathlib import Path

from .verification import value_survives_in_text


def write_pending_survival_trace(
    output_dir: str,
    derived_report: dict | None,
    must_include: list[dict],
) -> None:
    """Write a pending trace after obligations/curation select final items."""
    if not output_dir or not derived_report:
        return
    items = []
    for item in derived_report.get("items", []):
        if not isinstance(item, dict):
            continue
        created_ids = item.get("created_entry_ids") or []
        obligation_ids = _matching_must_include_ids(created_ids, must_include)
        items.append({
            "derived_work_id": item.get("id"),
            "created_entry_ids": created_ids,
            "obligation_ids": obligation_ids,
            "obligated": bool(obligation_ids),
            "curated": bool(obligation_ids),
            "target_files": item.get("target_deliverables") or [],
            "found_in_artifact": False,
            "artifact_locations": [],
            "death_mode": _pending_death_mode(item, obligation_ids),
            "expected_values": _expected_values(item),
            "expected_text": item.get("reason", ""),
        })
    trace = {
        "schema_version": 1,
        "items": items,
        "summary": _summarize(items),
    }
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    (swarm_dir / "commitment_survival_trace.pending.json").write_text(
        json.dumps(trace, indent=2),
        encoding="utf-8",
    )


def finalize_survival_trace(output_dir: str | Path,
                            artifact_texts: dict[str, str]) -> dict:
    """Finalize survival trace after deliverable files have been written."""
    out_dir = Path(output_dir)
    pending_path = out_dir / "swarm" / "commitment_survival_trace.pending.json"
    if not pending_path.exists():
        return {}
    trace = json.loads(pending_path.read_text(encoding="utf-8"))
    for item in trace.get("items", []):
        if not isinstance(item, dict):
            continue
        locations = _artifact_locations(item, artifact_texts)
        item["artifact_locations"] = locations
        item["found_in_artifact"] = bool(locations)
        if locations:
            item["death_mode"] = None
        elif not item.get("created_entry_ids"):
            item["death_mode"] = item.get("death_mode") or "executed_no_entry"
        elif not item.get("obligated"):
            item["death_mode"] = item.get("death_mode") or "not_obligated"
        else:
            item["death_mode"] = "artifact_missing"
    trace["summary"] = _summarize(trace.get("items", []))
    final_path = out_dir / "swarm" / "commitment_survival_trace.json"
    final_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
    return trace


def extract_artifact_texts(output_dir: str | Path,
                           deliverable_files: list[str]) -> dict[str, str]:
    output_path = Path(output_dir)
    texts: dict[str, str] = {}
    for filename in deliverable_files:
        path = output_path / filename
        if not path.exists():
            texts[filename] = ""
            continue
        suffix = path.suffix.lower()
        if suffix == ".docx":
            texts[filename] = _read_docx_text(path)
        elif suffix == ".xlsx":
            texts[filename] = _read_xlsx_text(path)
        elif suffix == ".pptx":
            texts[filename] = _read_pptx_text(path)
        else:
            texts[filename] = path.read_text(encoding="utf-8", errors="ignore")
    return texts


def _matching_must_include_ids(created_ids: list[str],
                               must_include: list[dict]) -> list[str]:
    created = set(created_ids)
    obligation_ids = []
    for idx, item in enumerate(must_include, 1):
        if not isinstance(item, dict):
            continue
        raw = item.get("entry_id") or item.get("entry_ids") or ""
        entry_ids = []
        if isinstance(raw, list):
            entry_ids = [str(v) for v in raw]
        else:
            entry_ids = [part.strip() for part in str(raw).split(",")]
        if created & set(entry_ids):
            obligation_ids.append(f"obl_{idx:03d}")
    return obligation_ids


def _pending_death_mode(item: dict, obligation_ids: list[str]) -> str | None:
    if item.get("status") in {"diagnostic_only", "executable"}:
        return "selected_but_not_executed"
    if item.get("status") == "execution_failed":
        return item.get("death_mode") or "executed_no_entry"
    if item.get("status") == "executed" and not obligation_ids:
        return "not_obligated"
    return None


def _expected_values(item: dict) -> list[str]:
    values = []
    for key in ("expected_result", "result", "expression"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
    return values


def _artifact_locations(item: dict,
                        artifact_texts: dict[str, str]) -> list[dict]:
    values = [v for v in item.get("expected_values", []) if isinstance(v, str)]
    context_terms = _context_terms(item)
    locations = []
    for filename, text in artifact_texts.items():
        for value in values:
            if value_survives_in_text(value, text, context_terms):
                locations.append({"file": filename, "evidence": value})
                break
    return locations


def _context_terms(item: dict) -> list[str]:
    words = []
    for raw in [item.get("expected_text", "")] + [
        str(inp.get("label", ""))
        for inp in item.get("required_inputs", [])
        if isinstance(inp, dict)
    ]:
        for word in str(raw).split():
            cleaned = "".join(ch for ch in word.lower() if ch.isalpha())
            if len(cleaned) >= 4 and cleaned not in words:
                words.append(cleaned)
            if len(words) >= 6:
                return words
    return words


def _summarize(items: list[dict]) -> dict:
    selected = len(items)
    created = sum(1 for item in items if item.get("created_entry_ids"))
    obligated = sum(1 for item in items if item.get("obligated"))
    survived = sum(1 for item in items if item.get("found_in_artifact"))
    death_modes: dict[str, int] = {}
    for item in items:
        mode = item.get("death_mode")
        if mode:
            death_modes[mode] = death_modes.get(mode, 0) + 1
    return {
        "selected": selected,
        "created": created,
        "obligated": obligated,
        "artifact_survived": survived,
        "lost": selected - survived,
        "death_modes": death_modes,
    }


def _read_docx_text(path: Path) -> str:
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        return ""


def _read_xlsx_text(path: Path) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), data_only=False, read_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append(f"# Sheet: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    parts.append(" | ".join(values))
        return "\n".join(parts)
    except Exception:
        return ""


def _read_pptx_text(path: Path) -> str:
    try:
        from pptx import Presentation
        prs = Presentation(str(path))
        parts = []
        for idx, slide in enumerate(prs.slides, 1):
            parts.append(f"# Slide {idx}")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text:
                    parts.append(shape.text)
        return "\n".join(parts)
    except Exception:
        return ""
