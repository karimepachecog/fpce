"""Machine-readable feature allow/deny contract (anti-leakage)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fpce.config import FEATURE_CONTRACT_JSON


@dataclass(frozen=True)
class FeatureContract:
    schema_version: int
    prediction_unit: str
    label: str
    decision_time_column: str
    split_column: str
    allow: frozenset[str]
    allow_from_time_grid: frozenset[str]
    deny: frozenset[str]
    deny_reasons: dict[str, str]
    raw: dict

    def assert_no_leakage(self, columns: Iterable[str]) -> None:
        leaked = sorted(set(columns) & self.deny)
        if leaked:
            reasons = "; ".join(
                f"{col} ({self.deny_reasons.get(col, 'denied')})" for col in leaked
            )
            raise ValueError(f"feature leakage: {reasons}")

    def allowed_columns(self, columns: Iterable[str]) -> list[str]:
        cols = list(columns)
        self.assert_no_leakage(cols)
        allowed = self.allow | self.allow_from_time_grid
        return [c for c in cols if c in allowed]


def load_feature_contract(path: Path | None = None) -> FeatureContract:
    path = path or FEATURE_CONTRACT_JSON
    raw = json.loads(path.read_text(encoding="utf-8"))
    return FeatureContract(
        schema_version=int(raw.get("schema_version", 1)),
        prediction_unit=str(raw["prediction_unit"]),
        label=str(raw["label"]),
        decision_time_column=str(raw["decision_time_column"]),
        split_column=str(raw["split_column"]),
        allow=frozenset(raw["allow"]),
        allow_from_time_grid=frozenset(
            raw.get("allow_from_time_grid_at_or_before_decision", [])
        ),
        deny=frozenset(raw["deny"]),
        deny_reasons=dict(raw.get("deny_reasons", {})),
        raw=raw,
    )
