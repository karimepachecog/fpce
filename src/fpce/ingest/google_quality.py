"""Quality report for Google 2019 attempt-level events."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fpce.config import (
    GOOGLE_ATTEMPTS_NAME,
    GOOGLE_MANIFEST_NAME,
    RACKS,
    REPORTS_DIR,
    repo_relpath,
    resolve_repo_path,
)
from fpce.ingest.instance_events import COSTING_WINDOW_THRESHOLDS
from fpce.ingest.google_events import _sql_str


def build_google_quality(attempts_path: Path) -> dict:
    """Aggregate attempt-level quality stats without loading the full table."""
    import duckdb

    if not attempts_path.exists():
        raise FileNotFoundError(f"Missing Google attempts: {attempts_path}")

    con = duckdb.connect()
    src = f"read_parquet({_sql_str(str(attempts_path))})"
    n, n_train, n_fail, n_cost, n_imputed = con.execute(
        f"""
        SELECT
          count(*),
          sum(CAST(eligible_for_training AS BIGINT)),
          sum(CAST(failed AS BIGINT)),
          sum(CAST(eligible_for_costing AS BIGINT)),
          sum(CAST(start_imputed AS BIGINT))
        FROM {src}
        """
    ).fetchone()
    n_train = int(n_train or 0)
    n_fail = int(n_fail or 0)
    terminal = {
        str(name): int(count)
        for name, count in con.execute(
            f"SELECT terminal_type, count(*) FROM {src} GROUP BY 1"
        ).fetchall()
    }
    per_instance = con.execute(
        f"""
        SELECT
          count(*),
          avg(n),
          quantile_cont(n, 0.5),
          quantile_cont(n, 0.95),
          max(n),
          avg(CASE WHEN n > 1 THEN 1.0 ELSE 0.0 END)
        FROM (
          SELECT collection_id, instance_index, count(*) AS n
          FROM {src}
          GROUP BY 1, 2
        )
        """
    ).fetchone()
    n_instances, mean_att, p50_att, p95_att, max_att, multi_frac = per_instance
    waste = con.execute(
        f"""
        SELECT
          min(waste_window_seconds),
          quantile_cont(waste_window_seconds, 0.5),
          quantile_cont(waste_window_seconds, 0.95),
          avg(waste_window_seconds)
        FROM {src}
        WHERE eligible_for_training = 1
        """
    ).fetchone()
    costing = {}
    for threshold in COSTING_WINDOW_THRESHOLDS:
        costing[str(threshold)] = int(
            con.execute(
                f"""
                SELECT count(*) FROM {src}
                WHERE outcome = 'failed'
                  AND waste_window_imputed = 0
                  AND waste_window_seconds >= {int(threshold)}
                """
            ).fetchone()[0]
        )
    frac = con.execute(
        f"""
        SELECT
          quantile_cont(plan_cpu_frac, 0.5),
          quantile_cont(plan_mem_frac, 0.5)
        FROM {src}
        WHERE eligible_for_training = 1
        """
    ).fetchone()

    rate = (n_fail / n_train) if n_train else 0.0
    report = {
        "prediction_unit": "attempt",
        "attempts_path": repo_relpath(attempts_path),
        "n_attempts": int(n or 0),
        "n_instances": int(n_instances or 0),
        "n_trainable": n_train,
        "n_failed": n_fail,
        "n_costable": int(n_cost or 0),
        "positive_rate_pct": round(rate * 100, 4),
        "start_imputed_rows": int(n_imputed or 0),
        "start_imputed_frac": round(float(n_imputed or 0) / int(n or 1), 4),
        "terminal_counts": terminal,
        "attempts_per_instance": {
            "mean": round(float(mean_att or 0), 3),
            "p50": float(p50_att or 0),
            "p95": float(p95_att or 0),
            "max": int(max_att or 0),
            "multi_attempt_frac": round(float(multi_frac or 0), 4),
        },
        "waste_window_seconds_trainable": {
            "min": None if waste[0] is None else round(float(waste[0]), 2),
            "p50": None if waste[1] is None else round(float(waste[1]), 2),
            "p95": None if waste[2] is None else round(float(waste[2]), 2),
            "mean": None if waste[3] is None else round(float(waste[3]), 2),
        },
        "costing_pool_by_threshold_seconds": costing,
        "plan_cpu_frac_p50": None if frac[0] is None else round(float(frac[0]), 5),
        "plan_mem_frac_p50": None if frac[1] is None else round(float(frac[1]), 5),
        "notes": (
            "Google ClusterData2019 cpus_request/memory_request are already "
            "fractions of the largest machine in the cell. Alibaba plan_cpu "
            "must be divided by 100 (hundredths of a core) then by cpu_num=96 "
            "before comparison. Memory has no equivalent physical divisor."
        ),
    }
    manifest = attempts_path.parent / GOOGLE_MANIFEST_NAME
    if manifest.exists():
        report["manifest"] = json.loads(manifest.read_text(encoding="utf-8"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write reports/google_quality.json from Google attempts.parquet"
    )
    parser.add_argument(
        "--attempts",
        type=Path,
        default=Path(RACKS["google"]["output_dir"]) / GOOGLE_ATTEMPTS_NAME,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORTS_DIR / "google_quality.json",
    )
    args = parser.parse_args()
    args.attempts = resolve_repo_path(args.attempts)
    report = build_google_quality(args.attempts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[ok] google quality -> {args.output}")
    print(
        f"  attempts={report['n_attempts']:,} "
        f"trainable={report['n_trainable']:,} "
        f"failed={report['n_failed']:,} "
        f"rate={report['positive_rate_pct']:.4f}% "
        f"costable={report['n_costable']:,} "
        f"multi_attempt={report['attempts_per_instance']['multi_attempt_frac']:.1%}"
    )


if __name__ == "__main__":
    main()
