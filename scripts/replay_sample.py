"""Re-run one or more samples from a samples.jsonl file with mp4 recording.

Usage:
    uv run python scripts/replay_sample.py data/runs/<ts>_sample/samples.jsonl 51 13 78
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import genesis as gs

from heron.walker.kneed import (
    InitialConditions,
    KneedParams,
    SimConfig,
    simulate,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay sampled (params, ic) tuples with mp4")
    parser.add_argument("samples_jsonl", type=Path)
    parser.add_argument("indices", type=int, nargs="+", help="sample indices to replay")
    parser.add_argument(
        "--seconds", type=float, default=None, help="override sim seconds (else 3s)"
    )
    parser.add_argument("--dt", type=float, default=0.001)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with args.samples_jsonl.open() as f:
        rows = {json.loads(line)["i"]: json.loads(line) for line in f}

    gs.init(backend=gs.metal)
    print(f"[heron] backend = {gs.backend}")

    seconds = args.seconds if args.seconds is not None else 3.0

    for idx in args.indices:
        if idx not in rows:
            print(f"[heron] WARNING: index {idx} not in {args.samples_jsonl}")
            continue
        row = rows[idx]
        params = KneedParams(**row["params"])
        ic = InitialConditions(**row["ic"])

        out_dir = Path("data/runs") / (time.strftime("%Y%m%d_%H%M%S") + f"_replay_i{idx}")
        out_dir.mkdir(parents=True, exist_ok=True)
        cfg = SimConfig(
            dt=args.dt,
            seconds=seconds,
            record_video=True,
            output_dir=out_dir,
        )
        print(f"[heron] replaying i={idx} -> {out_dir}")
        result = simulate(params, ic, cfg)
        print(
            f"  distance={result.distance:+.3f}m, "
            f"flips={result.n_stance_flips}, fell={result.fell}, "
            f"video={result.video_path}"
        )

        meta = {
            "replay_from": str(args.samples_jsonl),
            "original_index": idx,
            "params": asdict(params),
            "ic": asdict(ic),
            "result": {
                "distance": result.distance,
                "final_x": result.final_x,
                "final_z": result.final_z,
                "final_pitch": result.final_pitch,
                "n_stance_flips": result.n_stance_flips,
                "fell": result.fell,
            },
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        time.sleep(1)  # ensure unique timestamp dir per replay


if __name__ == "__main__":
    main()
