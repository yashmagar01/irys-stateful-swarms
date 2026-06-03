"""Deterministic verification for synthesis completeness.

LLM decides what matters (curation). Deterministic code verifies it's there.
Model-based verify only for items with no extractable exact values.
"""
from __future__ import annotations

import ast
import re


# --- Value extraction patterns ---

DOLLAR_PATTERN = re.compile(
    r'\$\s*\d+(?:\.\d+)?\s*[BMKbmk]'
    r'|\$\s*[\d,]+(?:\.\d+)?'
    r'|\d+(?:,\d{3})+\s*(?:dollars|USD)',
    re.IGNORECASE,
)

PERCENT_PATTERN = re.compile(
    r'\d+(?:\.\d+)?\s*%'
    r'|\d+(?:\.\d+)?\s*percent',
    re.IGNORECASE,
)

DATE_PATTERN = re.compile(
    r'(?:January|February|March|April|May|June|July|August|'
    r'September|October|November|December)'
    r'\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}'
    r'|\d{1,2}/\d{1,2}/\d{2,4}'
    r'|\d{4}-\d{2}-\d{2}',
    re.IGNORECASE,
)

SECTION_REF_PATTERN = re.compile(
    r'(?:Section|Article|Clause|Exhibit|Schedule)\s+[\d.]+(?:\([a-z]\))?',
    re.IGNORECASE,
)

NUMBER_UNIT_PATTERN = re.compile(
    r'\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:days?|months?|years?|shares?)\b',
    re.IGNORECASE,
)


def normalize_dollar(raw: str) -> int | None:
    s = raw.replace('$', '').replace(',', '').replace(' ', '').strip()
    multipliers = {'k': 1_000, 'm': 1_000_000, 'b': 1_000_000_000}
    for suffix, mult in multipliers.items():
        if s.lower().endswith(suffix):
            try:
                return int(float(s[:-1]) * mult)
            except ValueError:
                return None
    try:
        return int(float(s))
    except ValueError:
        return None


def extract_verification_targets(text: str) -> list[dict]:
    """Extract verifiable values from a must_include summary."""
    targets = []

    for m in DOLLAR_PATTERN.finditer(text):
        canonical = normalize_dollar(m.group())
        if canonical is not None:
            targets.append({
                "type": "dollar", "raw": m.group().strip(),
                "canonical": canonical,
            })

    for m in PERCENT_PATTERN.finditer(text):
        targets.append({"type": "percent", "raw": m.group().strip()})

    for m in DATE_PATTERN.finditer(text):
        targets.append({"type": "date", "raw": m.group().strip()})

    for m in SECTION_REF_PATTERN.finditer(text):
        targets.append({"type": "section_ref", "raw": m.group().strip()})

    for m in NUMBER_UNIT_PATTERN.finditer(text):
        targets.append({"type": "number_unit", "raw": m.group().strip()})

    return targets


def _dollar_present_in_text(canonical: int, text_lower: str) -> bool:
    """Check if a dollar amount (in any format) appears in the text."""
    for m in DOLLAR_PATTERN.finditer(text_lower):
        found = normalize_dollar(m.group())
        if found is not None and found == canonical:
            return True
    return False


def _value_present_with_context(draft_lower: str, value: str,
                                context_words: list[str],
                                window: int = 300) -> bool:
    """Check if value appears near context words in the draft."""
    val_lower = value.lower()
    idx = 0
    while True:
        pos = draft_lower.find(val_lower, idx)
        if pos == -1:
            return False
        if not context_words:
            return True
        snippet = draft_lower[max(0, pos - window):pos + len(val_lower) + window]
        if any(cw in snippet for cw in context_words):
            return True
        idx = pos + 1


def _extract_context_words(summary: str) -> list[str]:
    """Extract meaningful context words from a must_include summary."""
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
        'of', 'in', 'to', 'for', 'with', 'on', 'at', 'by', 'from',
        'and', 'or', 'not', 'that', 'this', 'but', 'if', 'as', 'has',
        'had', 'have', 'will', 'shall', 'may', 'must', 'should',
    }
    words = re.findall(r'[a-zA-Z]{3,}', summary.lower())
    return [w for w in words if w not in stop_words][:5]


def verify_deterministic(draft: str, must_include: list[dict]) -> tuple[list[int], list[int]]:
    """Verify must_include items deterministically.

    Returns (verified_indices, unresolved_indices).
    Verified = definitely present (exact match found).
    Unresolved = need model-based verification.
    """
    draft_lower = draft.lower()
    verified = []
    unresolved = []

    for i, item in enumerate(must_include):
        if isinstance(item, str):
            summary = item
        elif isinstance(item, dict):
            summary = item.get("summary", "")
        else:
            unresolved.append(i)
            continue

        if not summary:
            unresolved.append(i)
            continue

        targets = extract_verification_targets(summary)
        if not targets:
            unresolved.append(i)
            continue

        context_words = _extract_context_words(summary)
        all_found = True

        for target in targets:
            if target["type"] == "dollar":
                if not _dollar_present_in_text(target["canonical"], draft_lower):
                    all_found = False
                    break
            else:
                if not _value_present_with_context(
                    draft_lower, target["raw"], context_words
                ):
                    all_found = False
                    break

        if all_found:
            verified.append(i)
        else:
            unresolved.append(i)

    return verified, unresolved


def verify_calculation_expression(expression: str, expected: str) -> dict:
    """Verify a simple arithmetic expression against an expected numeric result."""
    expr = _normalize_expression(expression)
    expected_value = _parse_numeric_value(expected)
    if not expr or expected_value is None:
        return {
            "verified": None,
            "reason": "missing_expression_or_expected_value",
            "computed": None,
            "expected": expected_value,
        }
    try:
        computed = _safe_eval_numeric_expr(expr)
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError) as exc:
        return {
            "verified": None,
            "reason": f"could_not_evaluate_expression:{exc}",
            "computed": None,
            "expected": expected_value,
        }
    tolerance = max(0.01, abs(expected_value) * 0.005)
    return {
        "verified": abs(computed - expected_value) <= tolerance,
        "reason": "ok",
        "computed": computed,
        "expected": expected_value,
        "tolerance": tolerance,
    }


def value_survives_in_text(value: str, text: str,
                           context_terms: list[str] | None = None) -> bool:
    """Check whether a derived value appears in artifact text with optional context."""
    if not value or not text:
        return False
    context_terms = context_terms or []
    text_lower = text.lower()
    numeric = _parse_numeric_value(value)
    if numeric is not None:
        if value.strip().lower() in text_lower:
            return _value_present_with_context(
                text_lower, value, [c.lower() for c in context_terms if c],
            )
        dollar = normalize_dollar(value)
        if dollar is not None and _dollar_present_in_text(dollar, text_lower):
            return True
    return _value_present_with_context(
        text_lower, value, [c.lower() for c in context_terms if c],
    )


def _normalize_expression(expression: str) -> str:
    expr = expression.strip()
    if not expr:
        return ""
    expr = expr.replace("×", "*").replace("x", "*").replace("X", "*")
    expr = expr.replace("÷", "/").replace("−", "-").replace(",", "")
    expr = re.sub(r"\$\s*", "", expr)
    expr = re.sub(
        r"(\d+(?:\.\d+)?)\s*%",
        lambda m: f"({m.group(1)}/100)",
        expr,
    )
    return expr


def _parse_numeric_value(value: str) -> float | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    dollar = normalize_dollar(raw)
    if dollar is not None:
        return float(dollar)
    percent = re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?\s*%", raw)
    if percent:
        return float(percent.group().replace(",", "").replace("%", "")) / 100
    number = re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", raw)
    if not number:
        return None
    return float(number.group().replace(",", ""))


def _safe_eval_numeric_expr(expression: str) -> float:
    tree = ast.parse(expression, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = visit(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                if abs(right) > 10:
                    raise ValueError("exponent_too_large")
                return left ** right
        raise ValueError(f"unsupported_expression_node:{type(node).__name__}")

    return float(visit(tree))
