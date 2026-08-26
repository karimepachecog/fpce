"""Fit Fan et al. linear power model to MIT Supercloud GPU measurements.

The HPCA'22 DCGM release (`2022-hpca/dcgm.csv`, ~14 MB) records GPU power
draw and SM utilization. It does **not** contain node/CPU power, so it cannot
produce P_idle / P_peak for the Alibaba CPU rack.

This module checks whether the *shape* P = P_idle + (P_peak - P_idle) × u
describes measured (utilization, watts) pairs. Coefficients are reported as a
form-validation artifact, never merged into params/physical_cost.toml.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from fpce.config import DATA_RAW, REPORTS_DIR, repo_relpath

DCGM_URL = (
    "https://mit-supercloud-dataset.s3.us-west-2.amazonaws.com/2022-hpca/dcgm.csv"
)
DEFAULT_CSV = DATA_RAW / "supercloud" / "dcgm.csv"
DEFAULT_REPORT = REPORTS_DIR / "supercloud_fan_fit.json"


def download_dcgm(dest: Path = DEFAULT_CSV, force: bool = False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"[ok] supercloud dcgm already present ({dest.stat().st_size:,} bytes)")
        return dest
    print(f"[download] {DCGM_URL} -> {dest}")
    with requests.get(DCGM_URL, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
    print(f"[ok] downloaded {dest.stat().st_size:,} bytes")
    return dest


def fit_fan_model(
    utilization: np.ndarray,
    watts: np.ndarray,
) -> dict[str, float | int]:
    """Ordinary least squares for P = a + b × u, u in [0, 1].

    Fan identity: P_idle = a, P_peak = a + b (at u = 1).
    """
    u = np.asarray(utilization, dtype=float)
    p = np.asarray(watts, dtype=float)
    mask = np.isfinite(u) & np.isfinite(p) & (u >= 0) & (u <= 1) & (p > 0)
    u = u[mask]
    p = p[mask]
    if u.size < 10:
        raise ValueError(f"Need ≥10 valid (u, P) pairs, got {u.size}")
    # Design matrix [1, u]
    a_hat, b_hat = np.linalg.lstsq(np.column_stack([np.ones_like(u), u]), p, rcond=None)[0]
    y_hat = a_hat + b_hat * u
    ss_res = float(np.sum((p - y_hat) ** 2))
    ss_tot = float(np.sum((p - p.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    resid = p - y_hat
    return {
        "n": int(u.size),
        "p_idle_watts": float(a_hat),
        "p_peak_watts": float(a_hat + b_hat),
        "slope_watts": float(b_hat),
        "r_squared": float(r2),
        "mae_watts": float(np.mean(np.abs(resid))),
        "rmse_watts": float(np.sqrt(np.mean(resid**2))),
        "u_mean": float(u.mean()),
        "p_mean": float(p.mean()),
    }


def fit_from_dcgm(path: Path) -> dict:
    df = pd.read_csv(path)
    util_col = next(
        (c for c in ("smutilization_pct_avg", "avgsmutilization_pct") if c in df.columns),
        None,
    )
    power_col = next(
        (c for c in ("powerusage_watts_avg",) if c in df.columns),
        None,
    )
    if util_col is None or power_col is None:
        raise ValueError(
            f"dcgm.csv missing utilization/power columns; have {list(df.columns)}"
        )
    util = pd.to_numeric(df[util_col], errors="coerce") / 100.0
    watts = pd.to_numeric(df[power_col], errors="coerce")
    fit = fit_fan_model(util.to_numpy(), watts.to_numpy())
    fit.update(
        {
            "source": repo_relpath(path),
            "utilization_column": util_col,
            "power_column": power_col,
            "hardware": "GPU (nvidia DCGM); NOT Alibaba CPU servers",
            "transfers_coefficients": False,
            "note": (
                "Validates the linear Fan et al. form on measured GPU power. "
                "Do not copy p_idle/p_peak into physical_cost.toml."
            ),
        }
    )
    return fit


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit Fan et al. form to Supercloud GPU power (shape check only)"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()

    if not args.skip_download:
        download_dcgm(args.input, force=args.force_download)
    elif not args.input.exists():
        raise SystemExit(f"Missing {args.input}; omit --skip-download to fetch it")

    fit = fit_from_dcgm(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fit, indent=2), encoding="utf-8")
    print(
        f"[ok] Fan form on Supercloud GPU: R²={fit['r_squared']:.3f} "
        f"P_idle={fit['p_idle_watts']:.1f} W P_peak={fit['p_peak_watts']:.1f} W "
        f"n={fit['n']:,} -> {args.output}"
    )
    print("[note] GPU coefficients are NOT for Alibaba CPU costing")


if __name__ == "__main__":
    main()
