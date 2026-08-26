"""Configuration for Alibaba cluster-trace-v2018 ingestion."""

from __future__ import annotations

from pathlib import Path

# Repository paths (config lives at src/fpce/config.py → parents[2] = repo root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PARAMS_DIR = PROJECT_ROOT / "params"
PHYSICAL_COST_TOML = PARAMS_DIR / "physical_cost.toml"
FEATURE_CONTRACT_JSON = PARAMS_DIR / "feature_contract.json"
DOCS_DIR = PROJECT_ROOT / "docs"
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_INTERIM = PROJECT_ROOT / "data" / "interim"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
REPORTS_DIR = PROJECT_ROOT / "reports"
RACK_IDS_PATH = DATA_PROCESSED / "rack_machine_ids.json"
REPLICATION_RACK_IDS_PATH = DATA_PROCESSED / "replication_rack_machine_ids.json"

# Named racks for parameterized pipeline stages.
# The second rack is a *replication* check, not an out-of-distribution test: it is
# drawn from the same 8-day window with the same hardware spec, so its marginals
# match the primary rack closely (see reports/data_quality.md).
RACKS: dict[str, dict[str, Path | str]] = {
    "primary": {
        "ids_path": RACK_IDS_PATH,
        "output_dir": DATA_PROCESSED / "primary",
        "label": "primary training rack (failure domain 51)",
        "kind": "alibaba",
    },
    "replication": {
        "ids_path": REPLICATION_RACK_IDS_PATH,
        "output_dir": DATA_PROCESSED / "replication",
        "label": "held-out replication rack (failure domain 52, same window and hardware)",
        "kind": "alibaba",
    },
    "google": {
        "ids_path": DATA_PROCESSED / "google" / "export_manifest.json",
        "output_dir": DATA_PROCESSED / "google",
        "label": "Google cluster-data 2019 BigQuery export (cross-provider)",
        "kind": "external",
    },
}


def racks_of_kind(kind: str = "alibaba") -> dict[str, dict[str, Path | str]]:
    """Subset of RACKS. Ingest CLIs default to Alibaba; Google is opt-in."""
    return {name: cfg for name, cfg in RACKS.items() if cfg.get("kind", "alibaba") == kind}


def repo_relpath(path: Path | str) -> str:
    """POSIX path relative to the repo root, for JSON that other clones will read."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def resolve_repo_path(path: Path | str) -> Path:
    """Interpret a stored path as relative to PROJECT_ROOT unless it is absolute."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate

# Beijing public mirror (local DNS may fail; download.py resolves via 8.8.8.8)
OSS_BASE_URL = "http://clusterdata2018pubcn.oss-cn-beijing.aliyuncs.com"
OSS_HOSTNAME = "clusterdata2018pubcn.oss-cn-beijing.aliyuncs.com"

TRACE_FILES = {
    "machine_meta": {
        "filename": "machine_meta.tar.gz",
        "sha256": "b5b1b786b22cd413a3674b8f2ebfb2f02fac991c95df537f363ef2797c8f6d55",
        "csv_name": "machine_meta.csv",
    },
    "machine_usage": {
        "filename": "machine_usage.tar.gz",
        "sha256": "3e6ee87fd204bb85b9e234c5c75a5096580fdabc8f085b224033080090753a7a",
        "csv_name": "machine_usage.csv",
    },
    "batch_task": {
        "filename": "batch_task.tar.gz",
        "sha256": "7c4b32361bd1ec2083647a8f52a6854a03bc125ca5c202652316c499fbf978c6",
        "csv_name": "batch_task.csv",
    },
    "batch_instance": {
        "filename": "batch_instance.tar.gz",
        "sha256": "e73e5a9326669aa079ba20048ddd759383cabe1fe3e58620aa75bd034e2450c6",
        "csv_name": "batch_instance.csv",
    },
}

# CSV files have NO header row; columns from schema.txt
TABLE_SCHEMAS: dict[str, list[str]] = {
    "machine_meta": [
        "machine_id",
        "time_stamp",
        "failure_domain_1",
        "failure_domain_2",
        "cpu_num",
        "mem_size",
        "status",
    ],
    "machine_usage": [
        "machine_id",
        "time_stamp",
        "cpu_util_percent",
        "mem_util_percent",
        "mem_gps",
        "mkpi",
        "net_in",
        "net_out",
        "disk_io_percent",
    ],
    "batch_task": [
        "task_name",
        "instance_num",
        "job_name",
        "task_type",
        "status",
        "start_time",
        "end_time",
        "plan_cpu",
        "plan_mem",
    ],
    "batch_instance": [
        "instance_name",
        "task_name",
        "job_name",
        "task_type",
        "status",
        "start_time",
        "end_time",
        "machine_id",
        "seq_no",
        "total_seq_no",
        "cpu_avg",
        "cpu_max",
        "mem_avg",
        "mem_max",
    ],
}

# Normalized [0, 100] fields use -1 and 101 as invalid sentinels
SENTINEL_VALUES = {-1, 101}
NORMALIZED_COLUMNS = {
    "machine_meta": ["mem_size"],
    "machine_usage": [
        "cpu_util_percent",
        "mem_util_percent",
        "mem_gps",
        "net_in",
        "net_out",
        "disk_io_percent",
    ],
    "batch_task": ["plan_mem"],
    "batch_instance": ["mem_avg", "mem_max"],
}

# Rack selection defaults
RACK_SIZE = 40
TRACE_DURATION_SECONDS = 8 * 24 * 3600  # 8 days
RESAMPLE_INTERVAL_SECONDS = 60

# Alibaba plan_cpu is hundredths of a core. Homogeneous rack machines report cpu_num=96,
# so plan_cpu / 100 / 96 is a fraction of the machine — the same unit as Google
# ClusterData2019 resource_request.cpus (fraction of the largest machine in the cell).
ALI_CPU_NUM = 96
ALI_PLAN_CPU_HUNDREDTHS = 100.0

GOOGLE_RAW_DIR = DATA_RAW / "google"
GOOGLE_ATTEMPTS_NAME = "attempts.parquet"
GOOGLE_ATTEMPTS_SAMPLE_NAME = "attempts_sample.parquet"
GOOGLE_MANIFEST_NAME = "export_manifest.json"

# Instance statuses that imply the workload is still occupying the machine.
ACTIVE_STATUSES = {"Running", "Ready", "Waiting"}

# Failure-related statuses in batch workloads.
# Terminated = finished successfully; Waiting = not yet initialized (Alibaba docs).
# Interrupted = backup-instance stop; treated as failure when present in the trace.
FAILURE_STATUSES = {"Failed", "Interrupted"}
SUCCESS_STATUSES = {"Terminated"}

# --- Prediction target (instance level) -------------------------------------
# The modelling unit is a single batch instance, not a machine-minute. Machine-minute
# failure is not an anomaly in this trace: each machine sees ~550 instance failures
# over 8 days (one per ~21 min), giving a 36.8% positive rate at a 30-minute horizon,
# which a constant classifier matches. At instance level the positive rate is ~0.17%,
# and the counterfactual ("kill the doomed instance at decision_time") is actionable.
#
# Seconds after instance start at which the kill/keep decision is made. 0 = at
# admission, before any of the instance's own resource telemetry exists.
DECISION_OFFSET_SECONDS = 0

# Instances shorter than this are excluded: there is no useful waste window to save.
MIN_WASTE_WINDOW_SECONDS = 60

# Legacy machine-minute horizon. Retained only to build the auxiliary feature column
# seconds_to_next_failure on the machine grid; it is NOT the prediction target.
FAILURE_HORIZON_SECONDS = 30 * 60

# Time-based train/test split on primary rack (first 6 days train, last 2 days test).
TRAIN_FRACTION = 0.75
