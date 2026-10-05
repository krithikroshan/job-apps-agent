"""The shape of one posting's analysis, and the checks that hold the model
to it.

Everything the model returns is untrusted: it read a job advert anyone could
have written. Each field is type-checked, clamped to its range, and trimmed,
and anything unrecognised falls back to a neutral value rather than failing
the whole batch.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

SENIORITY = ("graduate", "entry", "junior", "mid", "senior", "unknown")
VISA = ("offered", "not_offered", "right_to_work_required", "not_mentioned")
MAX_LIST = 3
MAX_ITEM_CHARS = 160
MAX_SUMMARY_CHARS = 200


class AnalysisError(ValueError):
    """The model's reply couldn't be read at all."""


def _choice(value: Any, options: tuple[str, ...], default: str) -> str:
    return value if isinstance(value, str) and value in options else default


def _int(value: Any, lo: int, hi: int, *, clamp: bool) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    value = int(value)
    if clamp:
        return max(lo, min(hi, value))
    return value if lo <= value <= hi else None


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [_text(v, MAX_ITEM_CHARS) for v in value]
    return [i for i in items if i][:MAX_LIST]


def _date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def validate(raw: Any) -> dict:
    """One posting's analysis, cleaned. Raises :class:`AnalysisError` only
    when ``raw`` isn't an object at all."""
    if not isinstance(raw, dict):
        raise AnalysisError("an analysis wasn't a JSON object")
    visa = _choice(raw.get("visa"), VISA, "not_mentioned")
    evidence = _text(raw.get("visa_evidence"), MAX_ITEM_CHARS) or None
    return {
        "seniority": _choice(raw.get("seniority"), SENIORITY, "unknown"),
        "min_years": _int(raw.get("min_years"), 0, 15, clamp=False),
        "graduate_scheme": raw.get("graduate_scheme") is True,
        "qualifications": _texts(raw.get("qualifications")),
        "study_support": raw.get("study_support") is True,
        "visa": visa,
        # Evidence only makes sense for something the advert actually said.
        "visa_evidence": evidence if visa != "not_mentioned" else None,
        "deadline": _date(raw.get("deadline")),
        "fit": _int(raw.get("fit"), 0, 100, clamp=True),
        "fit_reasons": _texts(raw.get("fit_reasons")),
        "gaps": _texts(raw.get("gaps")),
        "red_flags": _texts(raw.get("red_flags")),
        "summary": _text(raw.get("summary"), MAX_SUMMARY_CHARS),
    }


#: "3", "POSTING 3", or the fence tag "posting-3-<marker>" the prompt wraps it in.
_ID_TEXT = re.compile(r"^(?:posting[\s-]*)?(\d{1,3})(?:-[0-9a-f]+)?$", re.IGNORECASE)


def _as_id(value: Any) -> int | None:
    """The posting number, in any of the forms a model echoes it back in."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and (m := _ID_TEXT.match(value.strip())):
        return int(m.group(1))
    return None


def parse_batch(text: str, ids: dict[int, str]) -> dict[str, dict]:
    """``{posting key: analysis}`` from a batch reply, using ``ids`` (the
    short numbers the prompt gave each posting) to map back. Entries with an
    unknown id or the wrong shape are dropped; a reply that isn't JSON at
    all raises :class:`AnalysisError`."""
    # Usually {"analyses": [...]}, but models sometimes reply with the bare
    # list; take whichever outer bracket comes first.
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise AnalysisError("the reply contained no JSON")
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    try:
        # NaN/Infinity aren't JSON, but Python would accept them: read as null.
        data = json.loads(text[start:end + 1], parse_constant=lambda _: None)
    except (json.JSONDecodeError, ValueError) as e:
        raise AnalysisError(f"the reply wasn't valid JSON: {e}") from e
    entries = data.get("analyses") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise AnalysisError("the reply had no 'analyses' list")

    out: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = ids.get(_as_id(entry.get("id")))
        if key and key not in out:
            try:
                out[key] = validate(entry)
            except (ValueError, OverflowError, TypeError):
                continue  # one garbled entry shouldn't cost the rest
    return out
