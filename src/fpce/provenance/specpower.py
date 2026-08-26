"""Scrape SPECpower_ssj2008 curves and derive idle/peak power envelope ranges.

This module does NOT map SPEC curves to individual Alibaba trace machines.
The trace anonymizes hardware identity. Published SPECpower results bound
P_idle and P_peak for a sensitivity sweep, optionally filtered to servers
whose thread count matches the homogeneous Alibaba rack (cpu_num=96).

Watts in multi-node submissions are the *aggregate* of `# of Identical Nodes`.
All envelope arithmetic uses per-node watts.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from fpce.config import DATA_PROCESSED, DATA_RAW

RESULTS_INDEX_URL = "https://www.spec.org/power_ssj2008/results/power_ssj2008.html"
RESULTS_BASE_URL = "https://www.spec.org/power_ssj2008/results/"
LOAD_LEVELS = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]
DEFAULT_OUTPUT = DATA_PROCESSED / "spec_power_curves.parquet"
DEFAULT_CACHE = DATA_RAW / "specpower_txt"
USER_AGENT = "fpce-specpower/0.1 (research; https://github.com/alibaba/clusterdata)"

# Alibaba cluster-trace-v2018 rack machines report cpu_num=96 (2 sockets).
ALI_THREADS = 96
ALI_CHIPS = 2
ALI_THREAD_TOLERANCE = 0.10


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    return sess


def _absolute_result_url(href: str) -> str | None:
    if not href.endswith(".txt") or "ssj2008" not in href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return urljoin("https://www.spec.org", href)
    return urljoin(RESULTS_BASE_URL, href)


def fetch_result_links(
    limit: int | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    sess = session or _session()
    resp = sess.get(RESULTS_INDEX_URL, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    unique: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        url = _absolute_result_url(a["href"])
        if url is None or url in seen:
            continue
        seen.add(url)
        unique.append(url)
        if limit is not None and len(unique) >= limit:
            break
    return unique


def _field(text: str, label: str) -> str | None:
    m = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", text, flags=re.M | re.I)
    return m.group(1).strip() if m else None


def _parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace(",", "")
    if not cleaned or cleaned in {"N/A", "NA", "None"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _first_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    m = re.search(r"(\d+)", raw.replace(",", ""))
    return int(m.group(1)) if m else None


def _parse_cpu_enabled(raw: str | None) -> tuple[int | None, int | None]:
    """Parse '56 cores, 2 chips, 28 cores/chip' → (cores, chips)."""
    if not raw:
        return None, None
    cores = None
    chips = None
    m_cores = re.search(r"(\d+)\s*cores?", raw, flags=re.I)
    m_chips = re.search(r"(\d+)\s*chips?", raw, flags=re.I)
    if m_cores:
        cores = int(m_cores.group(1))
    if m_chips:
        chips = int(m_chips.group(1))
    return cores, chips


def parse_result_txt(text: str, source_url: str) -> dict | None:
    watts: dict[str, float | None] = {str(level): None for level in LOAD_LEVELS}
    watts["Active Idle"] = None

    for level in LOAD_LEVELS:
        m = re.search(
            rf"^\s*{level}%\s*\|\s*[\d.,]+%\s*\|\s*[\d,]+\s*\|\s*([\d,]+(?:\.\d+)?)\s*\|",
            text,
            flags=re.M,
        )
        if m:
            watts[str(level)] = _parse_number(m.group(1))

    idle = re.search(
        r"^\s*Active Idle\s*\|\s*[\d,]+\s*\|\s*([\d,]+(?:\.\d+)?)\s*\|",
        text,
        flags=re.M,
    )
    if idle:
        watts["Active Idle"] = _parse_number(idle.group(1))

    if watts["100"] is None or watts["Active Idle"] is None:
        return None

    identical_nodes = _first_int(_field(text, "# of Identical Nodes")) or 1
    if identical_nodes < 1:
        identical_nodes = 1

    cores_enabled, chips = _parse_cpu_enabled(_field(text, "CPU(s) Enabled"))
    hardware_threads = _first_int(_field(text, "Hardware Threads"))
    memory_gb = _parse_number(_field(text, "Memory Amount (GB)"))
    if memory_gb is None:
        memory_gb = _parse_number(_field(text, "Memory amount (GB)"))

    raw_watts = {f"watts_{k.replace(' ', '_')}": v for k, v in watts.items()}
    per_node = {
        f"{col}_per_node": (None if val is None else round(val / identical_nodes, 4))
        for col, val in raw_watts.items()
    }

    return {
        "source_url": source_url,
        "system": _field(text, "Model") or _field(text, "Set Description"),
        "vendor": _field(text, "Hardware Vendor"),
        "cpu": _field(text, "CPU Name"),
        "identical_nodes": identical_nodes,
        "cores_enabled": cores_enabled,
        "chips": chips,
        "hardware_threads": hardware_threads,
        "memory_gb": memory_gb,
        **raw_watts,
        **per_node,
    }


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    name = url.rstrip("/").rsplit("/", 1)[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80]
    return cache_dir / f"{digest}_{safe}"


def _load_or_fetch(
    url: str,
    cache_dir: Path,
    session: requests.Session,
    sleep_s: float,
) -> str | None:
    path = _cache_path(cache_dir, url)
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        text = resp.text
    except requests.RequestException as exc:
        print(f"[warn] Failed to fetch {url}: {exc}")
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if sleep_s > 0:
        time.sleep(sleep_s)
    return text


def scrape_curves(
    limit: int | None = 15,
    cache_dir: Path | None = None,
    sleep_s: float = 0.15,
) -> pd.DataFrame:
    sess = _session()
    cache_dir = cache_dir or DEFAULT_CACHE
    rows: list[dict] = []
    links = fetch_result_links(limit=limit, session=sess)
    print(f"[ok] SPEC index: {len(links)} result links")
    for i, url in enumerate(links, start=1):
        text = _load_or_fetch(url, cache_dir, sess, sleep_s)
        if text is None:
            continue
        parsed = parse_result_txt(text, url)
        if parsed:
            rows.append(parsed)
            if i == 1 or i % 50 == 0 or i == len(links):
                print(f"[ok] parsed {i}/{len(links)} ({len(rows)} usable)")
        else:
            print(f"[warn] could not parse {url}")
    if not rows:
        raise RuntimeError("No SPEC Power curves parsed")
    return pd.DataFrame(rows)


def _idle_peak_columns(df: pd.DataFrame, per_node: bool = True) -> tuple[str, str]:
    if per_node and "watts_Active_Idle_per_node" in df.columns:
        return "watts_Active_Idle_per_node", "watts_100_per_node"
    return "watts_Active_Idle", "watts_100"


def summarize_power_envelope(df: pd.DataFrame, per_node: bool = True) -> dict[str, float]:
    """Reduce scraped curves to min/max idle and peak watts (per node by default)."""
    idle_col, peak_col = _idle_peak_columns(df, per_node=per_node)
    if idle_col not in df.columns or peak_col not in df.columns:
        raise ValueError(f"Expected columns {idle_col} and {peak_col}")
    idle = df[idle_col].dropna()
    peak = df[peak_col].dropna()
    if idle.empty or peak.empty:
        raise ValueError("No valid idle/peak watt values in SPEC curves")
    return {
        "p_idle_min": float(idle.min()),
        "p_idle_max": float(idle.max()),
        "p_peak_min": float(peak.min()),
        "p_peak_max": float(peak.max()),
        "n_systems": int(len(df)),
        "per_node": bool(per_node),
    }


def filter_matched_systems(
    df: pd.DataFrame,
    threads: int = ALI_THREADS,
    tolerance: float = ALI_THREAD_TOLERANCE,
    chips: int | None = ALI_CHIPS,
) -> pd.DataFrame:
    """Keep servers whose hardware-thread count is within ±tolerance of `threads`."""
    if "hardware_threads" not in df.columns:
        raise ValueError("hardware_threads column required for matched envelope")
    lo = threads * (1.0 - tolerance)
    hi = threads * (1.0 + tolerance)
    mask = df["hardware_threads"].between(lo, hi)
    if chips is not None and "chips" in df.columns:
        mask = mask & (df["chips"] == chips)
    return df.loc[mask].copy()


def summarize_matched_envelope(
    df: pd.DataFrame,
    threads: int = ALI_THREADS,
    tolerance: float = ALI_THREAD_TOLERANCE,
    chips: int | None = ALI_CHIPS,
    min_systems: int = 8,
) -> dict[str, float | int | str | bool]:
    """Envelope restricted to Alibaba-like 2-socket ~96-thread servers.

    If the matched subset is smaller than `min_systems`, still return it with
    `credible=False` so the caller can fall back to the generic envelope.
    """
    matched = filter_matched_systems(df, threads=threads, tolerance=tolerance, chips=chips)
    envelope = summarize_power_envelope(matched) if len(matched) else {
        "p_idle_min": float("nan"),
        "p_idle_max": float("nan"),
        "p_peak_min": float("nan"),
        "p_peak_max": float("nan"),
        "n_systems": 0,
        "per_node": True,
    }
    envelope.update(
        {
            "filter_threads": threads,
            "filter_tolerance": tolerance,
            "filter_chips": chips if chips is not None else -1,
            "credible": bool(len(matched) >= min_systems),
            "kind": "matched",
        }
    )
    return envelope


def envelope_matches_toml(
    envelope: dict,
    p_idle_min: float,
    p_idle_max: float,
    p_peak_min: float,
    p_peak_max: float,
    abs_tol: float = 0.6,
) -> bool:
    """True if TOML ranges match the envelope within rounding to 1 decimal."""
    pairs = (
        (envelope["p_idle_min"], p_idle_min),
        (envelope["p_idle_max"], p_idle_max),
        (envelope["p_peak_min"], p_peak_min),
        (envelope["p_peak_max"], p_peak_max),
    )
    return all(abs(float(a) - float(b)) <= abs_tol for a, b in pairs)


def emit_params_toml(envelope: dict) -> str:
    kind = envelope.get("kind", "generic")
    n = envelope["n_systems"]
    extra = ""
    if kind == "matched":
        extra = (
            f"# filter: {envelope.get('filter_chips')}-chip, "
            f"~{envelope.get('filter_threads')} hardware threads "
            f"(±{100 * float(envelope.get('filter_tolerance', 0)):.0f}%)\n"
        )
    return f"""# Fragment for params/physical_cost.toml — per-node SPEC envelope
# kind = {kind}
# n_systems = {n}
{extra}[power_model.p_idle_watts]
min = {envelope['p_idle_min']:.1f}
max = {envelope['p_idle_max']:.1f}

[power_model.p_peak_watts]
min = {envelope['p_peak_min']:.1f}
max = {envelope['p_peak_max']:.1f}
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape SPEC Power curves and derive idle/peak envelope"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max result files to fetch (default: all)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch every result linked from the SPEC index (overrides --limit)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.15,
        help="Seconds to wait between uncached fetches",
    )
    parser.add_argument(
        "--emit-params",
        action="store_true",
        help="Print TOML fragment for params/physical_cost.toml",
    )
    parser.add_argument(
        "--from-parquet",
        type=Path,
        default=None,
        help="Summarize envelope from existing parquet instead of scraping",
    )
    parser.add_argument(
        "--match-threads",
        type=int,
        default=ALI_THREADS,
        help="Target hardware-thread count for the matched envelope",
    )
    parser.add_argument(
        "--match-tolerance",
        type=float,
        default=ALI_THREAD_TOLERANCE,
        help="Relative tolerance around --match-threads",
    )
    args = parser.parse_args()

    if args.from_parquet:
        df = pd.read_parquet(args.from_parquet)
    else:
        limit = None if args.all else args.limit
        df = scrape_curves(limit=limit, cache_dir=args.cache_dir, sleep_s=args.sleep)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(args.output, index=False, engine="pyarrow")
        print(f"[ok] SPEC Power: {len(df)} curves -> {args.output}")

    generic = summarize_power_envelope(df)
    print(
        f"[ok] generic envelope (per-node): "
        f"P_idle [{generic['p_idle_min']:.1f}, {generic['p_idle_max']:.1f}] W, "
        f"P_peak [{generic['p_peak_min']:.1f}, {generic['p_peak_max']:.1f}] W "
        f"({generic['n_systems']} systems)"
    )

    matched = summarize_matched_envelope(
        df, threads=args.match_threads, tolerance=args.match_tolerance
    )
    print(
        f"[ok] matched envelope (~{args.match_threads} threads, "
        f"{ALI_CHIPS} chips, n={matched['n_systems']}, "
        f"credible={matched['credible']}): "
        f"P_idle [{matched['p_idle_min']:.1f}, {matched['p_idle_max']:.1f}] W, "
        f"P_peak [{matched['p_peak_min']:.1f}, {matched['p_peak_max']:.1f}] W"
    )
    chosen = matched if matched["credible"] else {**generic, "kind": "generic"}
    if args.emit_params:
        print(emit_params_toml(chosen))


if __name__ == "__main__":
    main()
