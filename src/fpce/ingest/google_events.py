"""Map a Google cluster-data 2019 instance-event export onto attempt-level rows.

Raw Borg `instance_events` are one row per lifecycle event, sharded arbitrarily
by BigQuery. 39% of (collection_id, instance_index) keys have more than one
terminal event because Borg reschedules after EVICT/KILL. Collapsing to one
row per instance therefore makes the label an arbitrary choice of first vs
last terminal.

The modelling unit is therefore an *attempt*: a SCHEDULE (or SUBMIT fallback)
paired with the next terminal event. `attempt_index` is the homolog of
Alibaba `seq_no`.

`time` is microseconds from 600 s before the trace start (ClusterData2019).
`type` uses the 2019 integer codes:

    0 SUBMIT, 1 QUEUE, 2 ENABLE, 3 SCHEDULE,
    4 EVICT, 5 FAIL, 6 FINISH, 7 KILL, 8 LOST,
    9 UPDATE_PENDING, 10 UPDATE_RUNNING

EVICT and KILL are recorded as `terminal_type` but excluded from training
by default, so a model trained on Alibaba Failed/Interrupted is evaluated
on genuine FAIL vs FINISH rather than on preemption.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

from fpce.config import (
    DATA_INTERIM,
    DECISION_OFFSET_SECONDS,
    GOOGLE_ATTEMPTS_NAME,
    GOOGLE_ATTEMPTS_SAMPLE_NAME,
    GOOGLE_MANIFEST_NAME,
    GOOGLE_RAW_DIR,
    MIN_WASTE_WINDOW_SECONDS,
    RACKS,
    repo_relpath,
    resolve_repo_path,
)

# ClusterData2019 instance_events.type
SUBMIT = 0
QUEUE = 1
ENABLE = 2
SCHEDULE = 3
EVICT = 4
FAIL = 5
FINISH = 6
KILL = 7
LOST = 8

TERMINAL_TYPES = {EVICT, FAIL, FINISH, KILL, LOST}
TERMINAL_NAME = {
    EVICT: "evicted",
    FAIL: "failed",
    FINISH: "succeeded",
    KILL: "killed",
    LOST: "lost",
}

US_PER_S = 1_000_000.0
DEFAULT_MEMORY_LIMIT = "2GB"

COLUMN_ALIASES = {
    "cpus_request": ("cpus_request", "request_cpus", "cpus"),
    "memory_request": ("memory_request", "request_memory", "memory"),
    "machine_id": ("machine_id", "machineId"),
    "priority": ("priority",),
    "scheduling_class": ("scheduling_class", "schedulingClass"),
}

REQUIRED = ("collection_id", "instance_index", "time", "type")


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _coalesce_expr(columns: set[str], aliases: tuple[str, ...]) -> str:
    present = [name for name in aliases if name in columns]
    if not present:
        return "NULL"
    if len(present) == 1:
        return present[0]
    return "COALESCE(" + ", ".join(present) + ")"


def _time_expr(unit: str) -> str:
    if unit == "s":
        return "CAST(time AS DOUBLE)"
    if unit == "us":
        return f"CAST(time AS DOUBLE) / {US_PER_S}"
    return (
        "CASE WHEN CAST(time AS DOUBLE) >= 1000000 "
        f"THEN CAST(time AS DOUBLE) / {US_PER_S} "
        "ELSE CAST(time AS DOUBLE) END"
    )


def build_attempts_sql(
    source: str,
    *,
    columns: Iterable[str] | None = None,
    time_unit: str = "us",
    decision_offset: int = DECISION_OFFSET_SECONDS,
    min_waste_window: int = MIN_WASTE_WINDOW_SECONDS,
    train_on: frozenset[str] = frozenset({"failed", "succeeded"}),
) -> str:
    """Return SQL that maps Borg events to one row per attempt.

    `source` is a DuckDB relation: a table name, `read_parquet('…')`, or a
    parenthesised subquery. Tests register a small frame as a table and pass
    that name so the same query runs in memory.
    """
    cols = set(columns) if columns is not None else {
        "collection_id",
        "instance_index",
        "time",
        "type",
        "machine_id",
        "cpus_request",
        "memory_request",
        "priority",
        "scheduling_class",
    }
    missing = [name for name in REQUIRED if name not in cols]
    if missing:
        raise ValueError(f"Google export missing columns: {missing}")

    cpus = _coalesce_expr(cols, COLUMN_ALIASES["cpus_request"])
    mem = _coalesce_expr(cols, COLUMN_ALIASES["memory_request"])
    machine = _coalesce_expr(cols, COLUMN_ALIASES["machine_id"])
    priority = _coalesce_expr(cols, COLUMN_ALIASES["priority"])
    sched_class = _coalesce_expr(cols, COLUMN_ALIASES["scheduling_class"])
    time_s = _time_expr(time_unit)
    train_list = ", ".join(_sql_str(name) for name in sorted(train_on))
    terminals = ",".join(str(code) for code in sorted(TERMINAL_TYPES))

    return f"""
WITH ev AS (
  SELECT
    collection_id,
    instance_index,
    {time_s} AS time_s,
    CAST(type AS INTEGER) AS type,
    {machine} AS machine_id,
    {cpus} AS cpus_request,
    {mem} AS memory_request,
    {priority} AS priority,
    {sched_class} AS scheduling_class
  FROM {source}
  WHERE CAST(type AS INTEGER) IN (0, 3, {terminals})
),
marked AS (
  SELECT
    collection_id,
    instance_index,
    time_s,
    type,
    max(CASE WHEN type = {SCHEDULE} THEN time_s END) OVER w AS sched_time,
    max(CASE WHEN type = {SUBMIT} THEN time_s END) OVER w AS submit_time,
    last_value(machine_id IGNORE NULLS) OVER w AS machine_filled,
    last_value(cpus_request IGNORE NULLS) OVER w AS cpus_filled,
    last_value(memory_request IGNORE NULLS) OVER w AS mem_filled,
    last_value(priority IGNORE NULLS) OVER w AS priority_filled,
    last_value(scheduling_class IGNORE NULLS) OVER w AS class_filled
  FROM ev
  WINDOW w AS (
    PARTITION BY collection_id, instance_index
    ORDER BY time_s, type
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  )
),
terminals AS (
  SELECT
    collection_id,
    instance_index,
    COALESCE(sched_time, submit_time, time_s) AS start_time,
    time_s AS end_time,
    type AS terminal_code,
    CASE WHEN sched_time IS NULL THEN 1 ELSE 0 END AS start_imputed,
    sched_time,
    submit_time,
    machine_filled AS machine_id,
    cpus_filled AS plan_cpu,
    mem_filled AS plan_mem,
    cpus_filled AS plan_cpu_frac,
    mem_filled AS plan_mem_frac,
    priority_filled AS priority,
    class_filled AS scheduling_class
  FROM marked
  WHERE type IN ({terminals})
  QUALIFY row_number() OVER (
    PARTITION BY collection_id, instance_index, COALESCE(sched_time, time_s)
    ORDER BY time_s
  ) = 1
)
SELECT
  collection_id,
  instance_index,
  CAST(
    row_number() OVER (
      PARTITION BY collection_id, instance_index
      ORDER BY start_time, end_time
    ) AS INTEGER
  ) AS attempt_index,
  machine_id,
  plan_cpu,
  plan_mem,
  plan_cpu_frac,
  plan_mem_frac,
  priority,
  scheduling_class,
  start_time,
  end_time,
  submit_time,
  sched_time,
  CASE terminal_code
    WHEN {EVICT} THEN 'evicted'
    WHEN {FAIL} THEN 'failed'
    WHEN {FINISH} THEN 'succeeded'
    WHEN {KILL} THEN 'killed'
    WHEN {LOST} THEN 'lost'
    ELSE 'censored'
  END AS terminal_type,
  CAST(CASE WHEN terminal_code = {FAIL} THEN 1 ELSE 0 END AS TINYINT) AS failed,
  CASE terminal_code
    WHEN {FAIL} THEN 'failed'
    WHEN {FINISH} THEN 'succeeded'
    WHEN {EVICT} THEN 'evicted'
    WHEN {KILL} THEN 'killed'
    WHEN {LOST} THEN 'lost'
    ELSE 'censored'
  END AS outcome,
  start_time + {int(decision_offset)} AS decision_time,
  end_time AS event_end,
  GREATEST(end_time - (start_time + {int(decision_offset)}), 0) AS waste_window_seconds,
  GREATEST(end_time - (start_time + {int(decision_offset)}), 0)
    AS waste_window_upper_bound_seconds,
  CAST(0 AS TINYINT) AS waste_window_imputed,
  CAST(start_imputed AS TINYINT) AS start_imputed,
  CAST(
    CASE WHEN CASE terminal_code
      WHEN {FAIL} THEN 'failed'
      WHEN {FINISH} THEN 'succeeded'
      WHEN {EVICT} THEN 'evicted'
      WHEN {KILL} THEN 'killed'
      WHEN {LOST} THEN 'lost'
      ELSE 'censored'
    END IN ({train_list}) THEN 1 ELSE 0 END
    AS TINYINT
  ) AS eligible_for_training,
  CAST(
    CASE
      WHEN terminal_code = {FAIL}
       AND GREATEST(end_time - (start_time + {int(decision_offset)}), 0)
           >= {int(min_waste_window)}
      THEN 1 ELSE 0
    END AS TINYINT
  ) AS eligible_for_costing
FROM terminals
""".strip()


def _connect(memory_limit: str, temp_dir: Path):
    import duckdb

    con = duckdb.connect()
    con.execute(f"SET memory_limit = {_sql_str(memory_limit)}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = {_sql_str(str(temp_dir))}")
    con.execute("SET preserve_insertion_order = false")
    return con


def _probe_columns(con, source: str) -> set[str]:
    rows = con.execute(f"SELECT * FROM {source} LIMIT 0").description
    return {col[0] for col in rows}


def build_google_attempts(
    events,
    *,
    decision_offset: int = DECISION_OFFSET_SECONDS,
    min_waste_window: int = MIN_WASTE_WINDOW_SECONDS,
    train_on: frozenset[str] = frozenset({"failed", "succeeded"}),
    time_unit: str = "us",
):
    """In-memory path: register a pandas frame and run the same SQL."""
    import duckdb
    import pandas as pd

    if not isinstance(events, pd.DataFrame):
        raise TypeError("events must be a pandas DataFrame")
    con = duckdb.connect()
    con.register("google_raw_events", events)
    sql = build_attempts_sql(
        "google_raw_events",
        columns=set(events.columns),
        time_unit=time_unit,
        decision_offset=decision_offset,
        min_waste_window=min_waste_window,
        train_on=train_on,
    )
    return con.execute(sql).df()


# Backwards-compatible name used by older tests and docs.
build_google_instance_events = build_google_attempts


def resolve_input(path: Path) -> tuple[str, list[Path]]:
    """Return a DuckDB FROM-clause and the concrete files behind it."""
    path = Path(path)
    if path.is_dir():
        shards = sorted(p for p in path.iterdir() if p.suffix.lower() in {".parquet", ".pq"})
        if not shards:
            raise FileNotFoundError(f"No parquet shards in {path}")
        glob = path / "*.parquet"
        return f"read_parquet({_sql_str(str(glob))})", shards
    if not path.exists():
        raise FileNotFoundError(f"Google export not found: {path}")
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return f"read_parquet({_sql_str(str(path))})", [path]
    if suffix == ".csv":
        return f"read_csv_auto({_sql_str(str(path))})", [path]
    raise ValueError(f"Unsupported Google export format: {path.suffix}")


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _shard_fingerprint(shards: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in shards:
        stat = path.stat()
        digest.update(f"{path.name}:{stat.st_size}\n".encode())
    return digest.hexdigest()


def write_manifest(
    path: Path,
    *,
    source: Path,
    shards: list[Path],
    output: Path,
    n_raw_events: int,
    event_type_counts: dict[str, int],
    n_attempts: int,
    terminal_counts: dict[str, int],
    n_train: int,
    n_fail: int,
    n_cost: int,
    time_unit: str,
) -> dict:
    payload = {
        "source": repo_relpath(source),
        "n_shards": len(shards),
        "shard_bytes": int(sum(p.stat().st_size for p in shards)),
        "shard_fingerprint": _shard_fingerprint(shards),
        "time_unit": time_unit,
        "n_raw_events": int(n_raw_events),
        "event_type_counts": event_type_counts,
        "n_attempts": int(n_attempts),
        "terminal_counts": terminal_counts,
        "n_trainable": int(n_train),
        "n_failed": int(n_fail),
        "n_costable": int(n_cost),
        "output": repo_relpath(output),
        "output_sha256": _sha256(output) if output.exists() else None,
        "prediction_unit": "attempt",
        "notes": (
            "One row per Borg attempt (SCHEDULE → next terminal). "
            "attempt_index is the homolog of Alibaba seq_no. "
            "plan_cpu_frac/plan_mem_frac are ClusterData2019 fractions of the "
            "largest machine in the cell; do not mix with raw Alibaba plan_cpu."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def run_attempts_etl(
    source_path: Path,
    output: Path,
    *,
    time_unit: str = "us",
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    temp_dir: Path | None = None,
    decision_offset: int = DECISION_OFFSET_SECONDS,
    min_waste_window: int = MIN_WASTE_WINDOW_SECONDS,
) -> dict:
    """Collapse a directory (or file) of Borg events to attempts.parquet."""
    from_clause, shards = resolve_input(source_path)
    temp = temp_dir or (DATA_INTERIM / "duckdb_tmp")
    con = _connect(memory_limit, temp)
    columns = _probe_columns(con, from_clause)
    sql = build_attempts_sql(
        from_clause,
        columns=columns,
        time_unit=time_unit,
        decision_offset=decision_offset,
        min_waste_window=min_waste_window,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    con.execute(
        f"COPY ({sql}) TO {_sql_str(str(output))} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )

    type_rows = con.execute(
        f"SELECT CAST(type AS INTEGER) AS type, count(*) "
        f"FROM {from_clause} GROUP BY 1 ORDER BY 1"
    ).fetchall()
    event_type_counts = {str(int(code)): int(n) for code, n in type_rows}

    stats = con.execute(
        f"""
        SELECT
          count(*),
          sum(CAST(eligible_for_training AS BIGINT)),
          sum(CAST(failed AS BIGINT)),
          sum(CAST(eligible_for_costing AS BIGINT))
        FROM read_parquet({_sql_str(str(output))})
        """
    ).fetchone()
    n_attempts, n_train, n_fail, n_cost = (int(v or 0) for v in stats)
    term_rows = con.execute(
        f"SELECT terminal_type, count(*) "
        f"FROM read_parquet({_sql_str(str(output))}) GROUP BY 1"
    ).fetchall()
    terminal_counts = {str(name): int(n) for name, n in term_rows}
    n_raw = int(sum(n for _, n in type_rows))

    manifest_path = output.parent / GOOGLE_MANIFEST_NAME
    return write_manifest(
        manifest_path,
        source=source_path,
        shards=shards,
        output=output,
        n_raw_events=n_raw,
        event_type_counts=event_type_counts,
        n_attempts=n_attempts,
        terminal_counts=terminal_counts,
        n_train=n_train,
        n_fail=n_fail,
        n_cost=n_cost,
        time_unit=time_unit,
    )


def write_stratified_trainable_sample(
    attempts_path: Path,
    output: Path,
    n_rows: int = 1_000_000,
    random_state: int = 0,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    temp_dir: Path | None = None,
) -> dict:
    """Write a FAIL+FINISH subsample that preserves the trainable positive rate.

    Streams PyArrow batches so a laptop does not need to hold 18.6M rows.
    ``memory_limit`` / ``temp_dir`` are unused (kept so the CLI can pass them).
    """
    _ = (memory_limit, temp_dir)
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    if not attempts_path.exists():
        raise FileNotFoundError(f"Missing Google attempts: {attempts_path}")

    dataset = ds.dataset(str(attempts_path), format="parquet")
    trainable = dataset.to_table(
        columns=["failed"],
        filter=pc.field("eligible_for_training") == 1,
    ).column("failed")
    n_train = trainable.length()
    n_fail = int(pc.sum(trainable).as_py() or 0)
    if n_train == 0:
        raise ValueError(f"No trainable rows in {attempts_path}")
    n_neg_all = n_train - n_fail
    rate = n_fail / n_train
    n_pos = min(n_fail, max(1, int(round(n_rows * rate))))
    n_neg = min(n_neg_all, max(0, n_rows - n_pos))
    pos_thresh = n_pos / max(n_fail, 1)
    neg_thresh = n_neg / max(n_neg_all, 1)
    seed = int(random_state)

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    writer: pq.ParquetWriter | None = None
    kept_fail = 0
    kept_ok = 0
    scanner = dataset.scanner(filter=pc.field("eligible_for_training") == 1)
    try:
        for batch in scanner.to_batches():
            cid = (
                batch.column("collection_id")
                .to_numpy(zero_copy_only=False)
                .astype("uint64", copy=False)
            )
            iix = (
                batch.column("instance_index")
                .to_numpy(zero_copy_only=False)
                .astype("uint64", copy=False)
            )
            att = (
                batch.column("attempt_index")
                .to_numpy(zero_copy_only=False)
                .astype("uint64", copy=False)
            )
            hashed = (
                cid * np.uint64(11400714819323198485)
                ^ iix * np.uint64(14029467366897019727)
                ^ att * np.uint64(1609587929392839161)
                ^ np.uint64(seed) * np.uint64(6364136223846793005)
            )
            unit = (hashed % np.uint64(1_000_000_007)).astype("float64") / 1_000_000_007
            failed = batch.column("failed").to_numpy(zero_copy_only=False)
            is_fail = failed == 1
            keep = (is_fail & (unit < pos_thresh)) | (~is_fail & (unit < neg_thresh))
            if int(keep.sum()) == 0:
                continue
            filtered = pa.Table.from_batches([batch]).filter(keep)
            n_fail_part = int(pc.sum(filtered.column("failed")).as_py() or 0)
            kept_fail += n_fail_part
            kept_ok += filtered.num_rows - n_fail_part
            if writer is None:
                writer = pq.ParquetWriter(output, filtered.schema, compression="zstd")
            writer.write_table(filtered)
    finally:
        if writer is not None:
            writer.close()
    got_n = kept_fail + kept_ok
    payload = {
        "source": repo_relpath(attempts_path),
        "output": repo_relpath(output),
        "n_rows": int(got_n),
        "n_failed": int(kept_fail),
        "positive_rate_pct": round((kept_fail / got_n) * 100, 4) if got_n else 0.0,
        "target_rows": int(n_rows),
        "source_trainable": int(n_train),
        "source_positive_rate_pct": round(rate * 100, 4),
        "random_state": seed,
        "notes": (
            "Stratified FAIL+FINISH subsample for laptop cross-provider eval. "
            "Use the full attempts.parquet for the final Role B pass."
        ),
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build attempts.parquet from a Google 2019 parquet-shard export"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=GOOGLE_RAW_DIR,
        help="Directory of parquet shards, or a single CSV/Parquet file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(RACKS["google"]["output_dir"]) / GOOGLE_ATTEMPTS_NAME,
    )
    parser.add_argument(
        "--time-unit",
        choices=("us", "s", "auto"),
        default="us",
        help="Unit of the time column (ClusterData2019 is microseconds)",
    )
    parser.add_argument("--memory-limit", default=DEFAULT_MEMORY_LIMIT)
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=DATA_INTERIM / "duckdb_tmp",
        help="Spill directory for DuckDB external sorts",
    )
    parser.add_argument(
        "--sample-from",
        type=Path,
        default=None,
        help="Skip the ETL and write a stratified laptop sample from this attempts parquet",
    )
    parser.add_argument(
        "--sample-out",
        type=Path,
        default=None,
        help="Path for attempts_sample.parquet (default: next to --output)",
    )
    parser.add_argument("--sample-rows", type=int, default=1_000_000)
    parser.add_argument("--sample-seed", type=int, default=0)
    args = parser.parse_args()
    args.input = resolve_repo_path(args.input)
    args.output = resolve_repo_path(args.output)
    if args.sample_from is not None:
        args.sample_from = resolve_repo_path(args.sample_from)
    if args.sample_out is not None:
        args.sample_out = resolve_repo_path(args.sample_out)

    sample_out = args.sample_out
    if sample_out is None:
        base = args.sample_from or args.output
        sample_out = Path(base).with_name(GOOGLE_ATTEMPTS_SAMPLE_NAME)

    if args.sample_from is not None:
        payload = write_stratified_trainable_sample(
            args.sample_from,
            sample_out,
            n_rows=args.sample_rows,
            random_state=args.sample_seed,
            memory_limit=args.memory_limit,
            temp_dir=args.temp_dir,
        )
        print(
            f"[ok] google sample: {payload['n_rows']:,} rows "
            f"(failed={payload['n_failed']:,} rate={payload['positive_rate_pct']:.4f}%) "
            f"-> {sample_out}"
        )
        return

    manifest = run_attempts_etl(
        args.input,
        args.output,
        time_unit=args.time_unit,
        memory_limit=args.memory_limit,
        temp_dir=args.temp_dir,
    )
    n = manifest["n_attempts"]
    n_train = manifest["n_trainable"]
    n_fail = manifest["n_failed"]
    rate = (n_fail / n_train * 100) if n_train else 0.0
    print(
        f"[ok] google attempts: {n:,} rows -> {args.output} "
        f"(training={n_train:,} failed={n_fail:,} rate={rate:.4f}% "
        f"types={manifest['terminal_counts']})"
    )
    print(f"[ok] manifest -> {args.output.parent / GOOGLE_MANIFEST_NAME}")
    payload = write_stratified_trainable_sample(
        args.output,
        sample_out,
        n_rows=args.sample_rows,
        random_state=args.sample_seed,
        memory_limit=args.memory_limit,
        temp_dir=args.temp_dir,
    )
    print(
        f"[ok] google sample: {payload['n_rows']:,} rows "
        f"(failed={payload['n_failed']:,} rate={payload['positive_rate_pct']:.4f}%) "
        f"-> {sample_out}"
    )


if __name__ == "__main__":
    main()
