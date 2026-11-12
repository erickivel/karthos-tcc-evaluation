"""Pure comparison functions for Karthos evaluation artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def counter_increase(values: Iterable[float]) -> float:
    iterator = iter(values)
    previous = next(iterator, None)
    if previous is None:
        return 0
    total = 0.0
    for current in iterator:
        if current < previous:
            total += current
        else:
            total += current - previous
        previous = current
    return total


def k6_requests(path: Path, start: str, end: str) -> dict:
    beginning, ending = parse_time(start), parse_time(end)
    if ending <= beginning:
        raise ValueError("end must be after start")
    by_route: dict[str, int] = {}
    total = errors = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            point = json.loads(line)
            if point.get("type") != "Point" or point.get("metric") != "http_reqs":
                continue
            data = point["data"]
            stamp = parse_time(data["time"])
            if stamp < beginning or stamp >= ending:
                continue
            tags = data.get("tags") or {}
            route = tags.get("route", "unknown")
            count = int(data.get("value", 1))
            total += count
            by_route[route] = by_route.get(route, 0) + count
            status = int(tags.get("status", "0"))
            if status == 0 or status >= 400:
                errors += count
    return {"total": total, "by_route": dict(sorted(by_route.items())), "errors": errors}


def paginate_documents(documents: Iterable[dict], key: str) -> list[dict]:
    items: list[dict] = []
    seen_tokens: set[str] = set()
    expected_token: str | None = None
    for index, document in enumerate(documents):
        if index > 0 and expected_token is None:
            raise ValueError("unexpected page after terminal page")
        items.extend(document.get("data", {}).get(key, []))
        token = document.get("next_token")
        if token:
            if token in seen_tokens:
                raise ValueError("repeated pagination token")
            seen_tokens.add(token)
        expected_token = token
    if expected_token:
        raise ValueError("missing final pagination page")
    return items


def compare_provenance(samples: Iterable[dict], source: str) -> dict[str, int]:
    wrong = missing = matching = 0
    for sample in samples:
        actual = (sample.get("labels") or {}).get("source")
        if actual is None:
            missing += 1
        elif actual != source:
            wrong += 1
        else:
            matching += 1
    return {"matching": matching, "wrong": wrong, "missing": missing}


def compare_entity_sets(expected: set[tuple], actual: set[tuple]) -> dict:
    return {
        "expected": len(expected),
        "actual": len(actual),
        "matching": len(expected & actual),
        "missing": sorted(expected - actual),
        "extra": sorted(actual - expected),
    }


def k6_quality(path: Path) -> dict:
    dropped = 0
    with path.open(encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            point = json.loads(line)
            if point.get('type') == 'Point' and point.get('metric') == 'dropped_iterations':
                dropped += int(point.get('data', {}).get('value', 0))
    return {'dropped_iterations': dropped}
