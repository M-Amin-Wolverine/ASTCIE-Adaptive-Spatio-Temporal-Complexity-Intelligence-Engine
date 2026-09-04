#!/usr/bin/env python3
"""
ASTCIE Wrapper – V8.1 + V9 Hybrid (Full Edition)
Features:
  - Rich colors + Progress bars + Logging
  - Parallel execution of V8.1 & V9 engines
  - Bitrate / CRF encoding modes
  - Single video / Multiple videos / Folder
  - Sequential multi-video
  - Parallel Batch mode (--parallel-batch) with aggregate Batch Complexity score
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Rich
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn, TextColumn,
        TimeElapsedColumn, TimeRemainingColumn, MofNCompleteColumn
    )
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("⚠️  rich نصب نیست → pip install rich")
    print("   اسکریپت بدون رنگ و progress کار می‌کند.\n")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
V81_SCRIPT = Path("v8/complexity7.py")
V9_SCRIPT  = Path("v9/complexity8.py")

WEIGHT_V81 = 0.5
WEIGHT_V9  = 0.5

console = Console() if RICH_AVAILABLE else None

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv"}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(out_dir: Path) -> logging.Logger:
    log_file = out_dir / "wrapper.log"
    logger = logging.getLogger("astcie")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    if RICH_AVAILABLE:
        ch = RichHandler(console=console, show_time=True, show_path=False, markup=True)
        ch.setLevel(logging.INFO)
    else:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
    logger.addHandler(ch)
    return logger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def run_cmd(cmd: list[str | Path], logger: logging.Logger | None = None) -> None:
    cmd_str = [str(x) for x in cmd]
    if logger:
        logger.debug("Running: " + " ".join(cmd_str))

    # Force UTF-8 to avoid Windows cp1252 UnicodeDecodeError
    result = subprocess.run(
        cmd_str,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"          # اگر کاراکتر ناشناخته بود، خراب نشه
    )

    if result.returncode:
        if logger:
            logger.error(f"Command failed ({result.returncode})")
            if result.stderr:
                logger.error(result.stderr.strip())
        raise SystemExit(result.returncode)


def find_result(video: Path, version: str) -> Path:
    stem = video.stem
    if version == "v81":
        roots = [Path("results"), Path(".")]
        names = ["result.json", "result_v81.json"]
    else:
        # V9 default output is results_v9
        roots = [Path("results_v9"), Path("result"), Path("results"), Path(".")]
        names = ["result_v9.json", "result.json"]

    for root in roots:
        for name in names:
            p = root / stem / name
            if p.exists():
                return p

    for root in roots:
        if root.exists():
            for name in names:
                for p in root.rglob(name):
                    if p.parent.name.lower() == stem.lower():
                        return p
    raise FileNotFoundError(f"{version} JSON not found for {video}")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pick(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return None


def extract_v81(data: dict) -> dict:
    c = pick(data, "v8_fusion_score", "complexity")
    b = pick(data, "bit_demand_index", "bdi", "BDI")
    if c is None or b is None:
        raise KeyError("V8.1 JSON missing v8_fusion_score or bit_demand_index")
    return {"complexity": to_float(c), "bdi": to_float(b), "raw": data}


def extract_v9(data: dict) -> dict:
    c = pick(data, "v9_fusion_score", "complexity")
    b = pick(data, "bit_demand_index", "bdi", "BDI")
    if c is None or b is None:
        raise KeyError("V9 JSON missing v9_fusion_score or bit_demand_index")
    return {
        "complexity": to_float(c),
        "bdi": to_float(b),
        "adci_temporal": to_float(pick(data, "adci_temporal")),
        "adci_spatial": to_float(pick(data, "adci_spatial")),
        "risk": to_float(pick(data, "risk")),
        "regime": pick(data, "content_regime", "regime"),
        "raw": data,
    }


def hybrid(v81: dict, v9: dict) -> dict:
    return {
        "weight_v81": WEIGHT_V81,
        "weight_v9": WEIGHT_V9,
        "complexity": WEIGHT_V81 * v81["complexity"] + WEIGHT_V9 * v9["complexity"],
        "bdi": WEIGHT_V81 * v81["bdi"] + WEIGHT_V9 * v9["bdi"],
    }


def ffprobe(video: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size,bit_rate",
        "-show_entries", "stream=index,codec_name,codec_type,width,height,r_frame_rate,avg_frame_rate,bit_rate,pix_fmt",
        "-of", "json", str(video)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def collect_videos(inputs: list[Path]) -> list[Path]:
    videos = []
    for p in inputs:
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
                    videos.append(f)
    return videos


# ---------------------------------------------------------------------------
# Core processing for one video
# ---------------------------------------------------------------------------
def process_single_video(
    video: Path,
    args: argparse.Namespace,
    out_root: Path,
    logger: logging.Logger,
    parallel_engines: bool = True,
) -> dict:
    """Process one video fully. Returns summary dict."""
    start_time = time.time()
    out_dir = out_root / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"▶ Processing: {video.name}")

    # --- Run engines (parallel or sequential) ---
    def run_v81():
        run_cmd([sys.executable, args.v81, video], logger)
        return extract_v81(load_json(find_result(video, "v81")))

    def run_v9():
        run_cmd([sys.executable, args.v9, video], logger)
        return extract_v9(load_json(find_result(video, "v9")))

    if parallel_engines:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(run_v81)
            f2 = ex.submit(run_v9)
            v81_orig = f1.result()
            v9_orig = f2.result()
    else:
        v81_orig = run_v81()
        v9_orig = run_v9()

    hybrid_orig = hybrid(v81_orig, v9_orig)

    # Target bitrate / CRF
    if args.encode_mode == "bitrate":
        if args.bitrate_mode == "fixed":
            target_bitrate = args.reference_bitrate
        else:
            target_bitrate = args.reference_bitrate * hybrid_orig["bdi"]
        encode_params = {
            "-b:v": f"{max(1, round(target_bitrate * 1000))}k",
            "-maxrate": f"{max(1, round(target_bitrate * 1000))}k",
            "-bufsize": f"{max(1, round(target_bitrate * 2000))}k",
        }
    else:  # crf
        target_bitrate = None
        encode_params = {"-crf": str(args.crf)}

    # Original contract
    contract = {
        "schema": "astcie-wrapper/v2",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input": str(video),
        "engines": {
            "v8_1": {"complexity": v81_orig["complexity"], "bdi": v81_orig["bdi"]},
            "v9": {k: v9_orig[k] for k in ("complexity", "bdi", "adci_temporal", "adci_spatial", "risk", "regime")},
        },
        "hybrid": hybrid_orig,
        "encoding_policy": {
            "encode_mode": args.encode_mode,
            "bitrate_mode": args.bitrate_mode if args.encode_mode == "bitrate" else None,
            "reference_bitrate_mbps": args.reference_bitrate,
            "selected_bitrate_mbps": target_bitrate,
            "crf": args.crf if args.encode_mode == "crf" else None,
        },
    }
    write_json(out_dir / "original_contract.json", contract)

    result = {
        "video": str(video),
        "hybrid_before": hybrid_orig["complexity"],
        "bdi_before": hybrid_orig["bdi"],
        "regime_before": v9_orig["regime"],
        "target_bitrate": target_bitrate,
        "success": True,
        "error": None,
        "processing_time_sec": None,
    }

    if args.no_encode:
        result["processing_time_sec"] = round(time.time() - start_time, 2)
        return result

    # Encode
    encoded = out_dir / f"{video.stem}_astcie.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", video,
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", args.preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
    ]
    for k, v in encode_params.items():
        cmd.extend([k, v])
    cmd.append(encoded)

    run_cmd(cmd, logger)
    probe_data = ffprobe(encoded)
    write_json(out_dir / "encoded_ffprobe.json", probe_data)

    # Re-analyze encoded (parallel)
    def run_v81_enc():
        run_cmd([sys.executable, args.v81, encoded], logger)
        return extract_v81(load_json(find_result(encoded, "v81")))

    def run_v9_enc():
        run_cmd([sys.executable, args.v9, encoded], logger)
        return extract_v9(load_json(find_result(encoded, "v9")))

    if parallel_engines:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(run_v81_enc)
            f2 = ex.submit(run_v9_enc)
            v81_enc = f1.result()
            v9_enc = f2.result()
    else:
        v81_enc = run_v81_enc()
        v9_enc = run_v9_enc()

    hybrid_enc = hybrid(v81_enc, v9_enc)

    # CSV row
    fmt = probe_data.get("format", {})
    streams = probe_data.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})

    row = {
        "video": str(video),
        "encoded": str(encoded),
        "v81_before": v81_orig["complexity"],
        "v9_before": v9_orig["complexity"],
        "hybrid_before": hybrid_orig["complexity"],
        "bdi_before": hybrid_orig["bdi"],
        "v81_after": v81_enc["complexity"],
        "v9_after": v9_enc["complexity"],
        "hybrid_after": hybrid_enc["complexity"],
        "bdi_after": hybrid_enc["bdi"],
        "delta_hybrid": hybrid_enc["complexity"] - hybrid_orig["complexity"],
        "delta_bdi": hybrid_enc["bdi"] - hybrid_orig["bdi"],
        "regime_before": v9_orig["regime"],
        "regime_after": v9_enc["regime"],
        "encoded_bitrate_mbps": (to_float(fmt.get("bit_rate")) or 0) / 1e6,
        "duration_s": to_float(fmt.get("duration")),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "codec": vstream.get("codec_name"),
    }
    with (out_dir / "comparison.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        w.writeheader()
        w.writerow(row)

    write_json(out_dir / "encoded_contract.json", {
        "engines": {
            "v8_1": {"complexity": v81_enc["complexity"], "bdi": v81_enc["bdi"]},
            "v9": {k: v9_enc[k] for k in ("complexity", "bdi", "adci_temporal", "adci_spatial", "risk", "regime")},
        },
        "hybrid": hybrid_enc,
    })

    result.update({
        "hybrid_after": hybrid_enc["complexity"],
        "bdi_after": hybrid_enc["bdi"],
        "regime_after": v9_enc["regime"],
        "delta_hybrid": hybrid_enc["complexity"] - hybrid_orig["complexity"],
        "processing_time_sec": round(time.time() - start_time, 2),
    })
    logger.info(f"✔ Finished: {video.name} ({result['processing_time_sec']}s)")
    return result


# ---------------------------------------------------------------------------
# Pretty print
# ---------------------------------------------------------------------------
def print_header(mode: str):
    if RICH_AVAILABLE:
        console.print(Panel.fit(
            f"[bold cyan]ASTCIE WRAPPER – Full Edition[/bold cyan]\n"
            f"[white]Mode: {mode}[/white]",
            border_style="cyan"
        ))
    else:
        print(f"\n=== ASTCIE WRAPPER ({mode}) ===\n")


def print_batch_summary(results: list[dict], batch_score: dict | None, out_dir: Path):
    if RICH_AVAILABLE:
        table = Table(title="📦 Batch Summary", box=box.DOUBLE_EDGE)
        table.add_column("Video", style="cyan")
        table.add_column("Hybrid Before", justify="right")
        table.add_column("Hybrid After", justify="right")
        table.add_column("Δ Hybrid", justify="right")
        table.add_column("Time (s)", justify="right")

        for r in results:
            if not r.get("success"):
                table.add_row(Path(r["video"]).name, "-", "-", "[red]FAIL[/red]", "-")
                continue
            delta = r.get("delta_hybrid", 0)
            color = "red" if delta > 0 else "green"
            table.add_row(
                Path(r["video"]).name,
                f"{r['hybrid_before']:.5f}",
                f"{r.get('hybrid_after', 0):.5f}",
                f"[{color}]{delta:+.5f}[/]",
                str(r.get("processing_time_sec", "-")),
            )
        console.print(table)

        if batch_score:
            console.print(Panel(
                f"[bold]Batch Complexity Score[/bold]: {batch_score['batch_complexity']:.6f}\n"
                f"Avg Hybrid Before : {batch_score['avg_hybrid_before']:.6f}\n"
                f"Avg Hybrid After  : {batch_score['avg_hybrid_after']:.6f}\n"
                f"Videos processed  : {batch_score['count']}\n"
                f"Total time        : {batch_score['total_time_sec']:.1f}s",
                title="🧮 Aggregate Batch Metrics",
                border_style="green"
            ))
        console.print(f"\n[bold green]Output root:[/] {out_dir}")
    else:
        print("\n=== BATCH SUMMARY ===")
        for r in results:
            print(f"{Path(r['video']).name}: {r.get('hybrid_before')} → {r.get('hybrid_after')}")
        if batch_score:
            print(f"Batch Complexity: {batch_score['batch_complexity']:.6f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ASTCIE V8.1 + V9 Full Wrapper (Parallel + Multi-video + CRF)"
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="Video file(s) or folder(s)")
    parser.add_argument("--v81", type=Path, default=V81_SCRIPT)
    parser.add_argument("--v9", type=Path, default=V9_SCRIPT)
    parser.add_argument("--out", type=Path, default=Path("wrapper_results"))
    parser.add_argument("--reference-bitrate", type=float, default=30.0)
    parser.add_argument("--bitrate-mode", choices=["bdi", "fixed"], default="bdi")
    parser.add_argument("--encode-mode", choices=["bitrate", "crf"], default="bitrate",
                        help="bitrate or crf encoding")
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--preset", default="medium")
    parser.add_argument("--no-encode", action="store_true")
    parser.add_argument("--no-parallel-engines", action="store_true",
                        help="Disable parallel V8.1 + V9 (run sequentially)")
    parser.add_argument("--parallel-batch", action="store_true",
                        help="Enable parallel processing of multiple videos (requires multiple inputs)")
    parser.add_argument("--max-workers", type=int, default=2,
                        help="Max parallel videos when --parallel-batch is used")

    args = parser.parse_args()

    # Safety checks
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not found in PATH")
    if not args.v81.exists() or not args.v9.exists():
        raise FileNotFoundError("complexity7.py or complexity8.py not found")

    videos = collect_videos(args.inputs)
    if not videos:
        raise FileNotFoundError("No valid video files found")

    # Mode decision
    is_batch = len(videos) > 1
    use_parallel_batch = args.parallel_batch and is_batch

    if args.parallel_batch and not is_batch:
        print("⚠️  --parallel-batch ignored (only one video)")
        use_parallel_batch = False

    mode_str = "Single Video"
    if is_batch and use_parallel_batch:
        mode_str = f"Parallel Batch ({len(videos)} videos, max_workers={args.max_workers})"
    elif is_batch:
        mode_str = f"Sequential Batch ({len(videos)} videos)"

    print_header(mode_str)

    out_root = args.out
    out_root.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(out_root)
    logger.info(f"Mode: {mode_str} | Videos: {len(videos)}")

    results = []
    total_start = time.time()

    if use_parallel_batch:
        # Parallel batch processing
        logger.info("Starting PARALLEL BATCH processing")
        with ProcessPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {
                executor.submit(
                    process_single_video,
                    video, args, out_root, logger,
                    parallel_engines=not args.no_parallel_engines
                ): video for video in videos
            }
            for future in as_completed(futures):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    vid = futures[future]
                    logger.error(f"Failed {vid.name}: {e}")
                    results.append({"video": str(vid), "success": False, "error": str(e)})
    else:
        # Sequential (single or multi)
        for video in videos:
            try:
                res = process_single_video(
                    video, args, out_root, logger,
                    parallel_engines=not args.no_parallel_engines
                )
                results.append(res)
            except Exception as e:
                logger.error(f"Failed {video.name}: {e}")
                results.append({"video": str(video), "success": False, "error": str(e)})

    total_time = round(time.time() - total_start, 2)

    # Aggregate Batch Score (only meaningful in multi-video)
    batch_score = None
    successful = [r for r in results if r.get("success") and "hybrid_before" in r]
    if len(successful) >= 2:
        avg_before = sum(r["hybrid_before"] for r in successful) / len(successful)
        avg_after = sum(r.get("hybrid_after", r["hybrid_before"]) for r in successful) / len(successful)
        # Batch Complexity: weighted by processing pressure (simple version)
        batch_complexity = avg_before * (1 + 0.15 * (len(successful) / max(args.max_workers, 1)))
        batch_score = {
            "batch_complexity": batch_complexity,
            "avg_hybrid_before": avg_before,
            "avg_hybrid_after": avg_after,
            "count": len(successful),
            "total_time_sec": total_time,
            "parallel": use_parallel_batch,
        }
        write_json(out_root / "batch_aggregate.json", batch_score)

    print_batch_summary(results, batch_score, out_root)
    logger.info(f"All done. Total time: {total_time}s")


if __name__ == "__main__":
    main()