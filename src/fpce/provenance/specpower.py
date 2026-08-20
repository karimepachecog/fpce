"""Scrape SPECpower_ssj2008 curves and derive idle/peak power envelope ranges.

This module does NOT map SPEC curves to individual Alibaba trace machines.
The trace anonymizes hardware identity (proposal line 31). Instead we use
published SPECpower results to bound P_idle and P_peak for a sensitivity sweep
across comparable server classes.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from fpce.config import DATA_PROCESSED, PROJECT_ROOT

RESULTS_URL = "https://www.spec.org/power_ssj2008/results/power_ssj2008.html"
LOAD_LEVELS = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]
DEFAULT_OUTPUT = DATA_PROCESSED / "spec_power_curves.parquet"


def fetch_result_links(limit: int = 15) -> list[str]:
    resp = requests.get(RESULTS_URL, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith(".txt") and "ssj2008" in href:
            if href.startswith("http"):
                links.append(href)
            else:
                links.append(f"https://www.spec.org{href}")
    seen: set[str] = set()
    unique: list[str] = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)
        if len(unique) >= limit:
            break
    return unique


def _field(text: str, label: str) -> str | None:
    m = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", text, flags=re.M)
    return m.group(1).strip() if m else None


def parse_result_txt(text: str, source_url: str) -> dict | None:
    watts: dict[str, float | None] = {str(level): None for level in LOAD_LEVELS}
    watts["Active Idle"] = None

    for level in LOAD_LEVELS:
        m = re.search(
            rf"^\s*{level}%\s*\|\s*[\d.]+%\s*\|\s*[\d,]+\s*\|\s*([\d.]+)\s*\|",
            text,
            flags=re.M,
        )
        if m:
            watts[str(level)] = float(m.group(1))

    idle = re.search(
        r"^\s*Active Idle\s*\|\s*[\d,]+\s*\|\s*([\d.]+)\s*\|",
        text,
        flags=re.M,
    )
    if idle:
        watts["Active Idle"] = float(idle.group(1))

    if watts["100"] is None or watts["Active Idle"] is None:
        return None

    return {
        "source_url": source_url,
        "system": _field(text, "Model") or _field(text, "Set Description"),
        "vendor": _field(text, "Hardware Vendor"),
        "cpu": _field(text, "CPU Name"),
        **{f"watts_{k.replace(' ', '_')}": v for k, v in watts.items()},
    }


def scrape_curves(limit: int = 15) -> pd.DataFrame:
    rows: list[dict] = []
    for url in fetch_result_links(limit=limit):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            parsed = parse_result_txt(resp.text, url)
            if parsed:
                rows.append(parsed)
                print(f"[ok] parsed {url}")
            else:
                print(f"[warn] could not parse {url}")
        except requests.RequestException as exc:
            print(f"[warn] Failed to fetch {url}: {exc}")
    if not rows:
        raise RuntimeError("No SPEC Power curves parsed")
    return pd.DataFrame(rows)


def summarize_power_envelope(df: pd.DataFrame) -> dict[str, float]:
    """Reduce scraped curves to min/max idle and peak watts for sweep bounds."""
    idle_col = "watts_Active_Idle"
    peak_col = "watts_100"
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
    }


def emit_params_toml(envelope: dict[str, float]) -> str:
    return f"""# Fragment for params/physical_cost.toml — SPEC envelope (not per-machine mapping)
[power_model.p_idle_watts]
min = {envelope['p_idle_min']:.1f}
max = {envelope['p_idle_max']:.1f}

[power_model.p_peak_watts]
min = {envelope['p_peak_min']:.1f}
max = {envelope['p_peak_max']:.1f}
# n_systems = {envelope['n_systems']}
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape SPEC Power curves and derive idle/peak envelope"
    )
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--emit-params",
        action="store_true",
        help="Print TOML fragment for params/physical_cost.toml from scraped envelope",
    )
    parser.add_argument(
        "--from-parquet",
        type=Path,
        default=None,
        help="Summarize envelope from existing parquet instead of scraping",
    )
    args = parser.parse_args()

    if args.from_parquet:
        df = pd.read_parquet(args.from_parquet)
    else:
        df = scrape_curves(limit=args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(args.output, index=False, engine="pyarrow")
        print(f"[ok] SPEC Power: {len(df)} curves -> {args.output}")

    envelope = summarize_power_envelope(df)
    print(
        f"[ok] envelope: P_idle [{envelope['p_idle_min']:.1f}, {envelope['p_idle_max']:.1f}] W, "
        f"P_peak [{envelope['p_peak_min']:.1f}, {envelope['p_peak_max']:.1f}] W "
        f"({envelope['n_systems']} systems)"
    )
    if args.emit_params:
        print(emit_params_toml(envelope))


if __name__ == "__main__":
    main()
