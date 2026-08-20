"""Download Alibaba cluster-trace-v2018 files with resume and checksum verification."""

from __future__ import annotations

import argparse
import hashlib
import socket
import subprocess
import tarfile
from pathlib import Path

from tqdm import tqdm

import re

from fpce.config import DATA_RAW, OSS_BASE_URL, OSS_HOSTNAME, TRACE_FILES

_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

# Local DNS may fail for aliyuncs.com; resolve via public DNS once at import
_RESOLVE_IP: str | None = None


def _resolve_host_ip(hostname: str) -> str:
    global _RESOLVE_IP
    if _RESOLVE_IP:
        return _RESOLVE_IP
    try:
        result = subprocess.run(
            ["dig", "@8.8.8.8", "+short", hostname, "A"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        for line in result.stdout.strip().splitlines():
            ip = line.strip().rstrip(".")
            if _IP_RE.match(ip):
                _RESOLVE_IP = ip
                return ip
    except (subprocess.SubprocessError, IndexError):
        pass
    ip = socket.gethostbyname(hostname)
    _RESOLVE_IP = ip
    return ip


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(name: str, force: bool = False) -> Path:
    meta = TRACE_FILES[name]
    dest = DATA_RAW / meta["filename"]
    DATA_RAW.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not force:
        actual = sha256_file(dest)
        if actual == meta["sha256"]:
            print(f"[ok] {name}: checksum verified ({dest.stat().st_size:,} bytes)")
            return dest
        print(f"[warn] {name}: checksum mismatch, re-downloading")

    url = f"{OSS_BASE_URL}/{meta['filename']}"
    ip = _resolve_host_ip(OSS_HOSTNAME)

    cmd = [
        "curl",
        "-C",
        "-",
        "-L",
        "--fail",
        "--retry",
        "5",
        "--retry-delay",
        "3",
        "--connect-timeout",
        "30",
        "--max-time",
        "0",
        "--resolve",
        f"{OSS_HOSTNAME}:80:{ip}",
        "-o",
        str(dest),
        url,
    ]
    print(f"[download] {name} -> {dest}")
    subprocess.run(cmd, check=True)

    actual = sha256_file(dest)
    if actual != meta["sha256"]:
        raise ValueError(
            f"Checksum mismatch for {name}: expected {meta['sha256']}, got {actual}"
        )
    print(f"[ok] {name}: downloaded and verified ({dest.stat().st_size:,} bytes)")
    return dest


def extract_tar(name: str) -> Path:
    meta = TRACE_FILES[name]
    archive = DATA_RAW / meta["filename"]
    csv_path = DATA_RAW / meta["csv_name"]
    if csv_path.exists():
        print(f"[skip] {name}: {csv_path} already extracted")
        return csv_path

    print(f"[extract] {name}: {archive}")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in tqdm(members, desc=f"extract {name}"):
            tar.extract(member, path=DATA_RAW, filter="data")

    if not csv_path.exists():
        raise FileNotFoundError(f"Expected CSV not found after extract: {csv_path}")
    return csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Alibaba cluster trace files")
    parser.add_argument(
        "--files",
        nargs="+",
        choices=list(TRACE_FILES.keys()),
        default=["machine_meta", "machine_usage", "batch_task"],
        help="Which trace files to download",
    )
    parser.add_argument("--extract", action="store_true", help="Extract tar.gz after download")
    parser.add_argument("--force", action="store_true", help="Force re-download")
    args = parser.parse_args()

    for name in args.files:
        download_file(name, force=args.force)
        if args.extract:
            extract_tar(name)


if __name__ == "__main__":
    main()
