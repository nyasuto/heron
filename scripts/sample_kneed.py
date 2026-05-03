"""Phase 2.5/2.6: random parameter sampling for the kneed walker.

Samples N (params, ic) tuples uniformly from configurable ranges, runs simulate()
on each (in parallel via multiprocessing), writes per-sample results to
samples.jsonl, and aggregates a summary.json. Smoke test for the Phase 3
(MAP-Elites) batching path.

Parallelism: uses spawn-context multiprocessing.Pool. Each worker initializes
Genesis once and processes samples handed to it via imap_unordered. Spawn
(rather than fork) is required on macOS to avoid MPS/Metal corruption when
forking after Genesis has already been imported in the parent.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import time
from dataclasses import asdict
from pathlib import Path

from heron.walker.kneed import (
    InitialConditions,
    KneedParams,
    SimConfig,
    simulate,
)


def sample_params(rng: random.Random) -> KneedParams:
    return KneedParams(
        thigh_length=rng.uniform(0.3, 0.7),
        shin_length=rng.uniform(0.3, 0.7),
        thigh_mass=rng.uniform(1.5, 4.0),
        shin_mass=rng.uniform(1.0, 3.5),
        hip_mass=rng.uniform(5.0, 20.0),
        foot_radius=rng.uniform(0.02, 0.05),
        knee_damping=rng.uniform(0.1, 1.0),
        slope_deg=rng.uniform(2.0, 8.0),
    )


def sample_ic(rng: random.Random) -> InitialConditions:
    return InitialConditions(
        stance_q=rng.uniform(0.10, 0.30),
        swing_q=rng.uniform(-0.40, -0.15),
        stance_qdot=rng.uniform(-2.0, -0.5),
        swing_qdot=rng.uniform(-1.5, 0.5),
        swing_knee_q=rng.uniform(0.0, 0.6),
        swing_knee_qdot=rng.uniform(-1.0, 0.5),
    )


def _worker_init() -> None:
    """Initialize Genesis once per worker process."""
    import genesis as gs

    gs.init(backend=gs.metal)


def _run_one(task: tuple[int, KneedParams, InitialConditions, SimConfig]) -> tuple:
    i, params, ic, cfg = task
    t0 = time.perf_counter()
    result = simulate(params, ic, cfg)
    wall = time.perf_counter() - t0
    return (i, params, ic, result, wall)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Heron Phase 2.5/2.6 random sampling")
    parser.add_argument("--n-samples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--dt", type=float, default=0.001)
    parser.add_argument(
        "--n-procs",
        type=int,
        default=8,
        help="Worker processes (spawn-context). 1 = sequential in-process.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)

    out_dir = Path("data/runs") / (time.strftime("%Y%m%d_%H%M%S") + "_sample")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[heron] seed = {args.seed}, n_procs = {args.n_procs}")
    print(f"[heron] running {args.n_samples} samples, output -> {out_dir}")

    # Build all tasks up front so the RNG sequence is deterministic regardless of
    # worker scheduling. Workers receive (params, ic, cfg) — they don't see the rng.
    tasks: list[tuple[int, KneedParams, InitialConditions, SimConfig]] = []
    for i in range(args.n_samples):
        params = sample_params(rng)
        ic = sample_ic(rng)
        cfg = SimConfig(
            dt=args.dt,
            seconds=args.seconds,
            record_video=False,
            record_trajectory=False,
        )
        tasks.append((i, params, ic, cfg))

    samples_path = out_dir / "samples.jsonl"
    fell_count = 0
    distance_sum = 0.0
    distance_max = 0.0
    wall_sum = 0.0
    flips_max = 0
    completed = 0

    t_total = time.perf_counter()
    rows_by_i: dict[int, dict] = {}

    if args.n_procs <= 1:
        # Sequential path (also handy for debugging without multiprocessing noise).
        _worker_init()
        results_iter = (_run_one(t) for t in tasks)
    else:
        ctx = mp.get_context("spawn")
        pool = ctx.Pool(processes=args.n_procs, initializer=_worker_init)
        results_iter = pool.imap_unordered(_run_one, tasks)

    try:
        for i, params, ic, result, wall in results_iter:
            row = {
                "i": i,
                "params": asdict(params),
                "ic": asdict(ic),
                "result": {
                    "distance": result.distance,
                    "final_x": result.final_x,
                    "final_z": result.final_z,
                    "final_pitch": result.final_pitch,
                    "fell": result.fell,
                    "n_stance_flips": result.n_stance_flips,
                    "stance_left_fraction": result.stance_left_fraction,
                    "wall_seconds": wall,
                },
            }
            rows_by_i[i] = row

            if result.fell:
                fell_count += 1
            distance_sum += result.distance
            if result.distance > distance_max:
                distance_max = result.distance
            wall_sum += wall
            if result.n_stance_flips > flips_max:
                flips_max = result.n_stance_flips
            completed += 1

            if completed % 5 == 0 or completed == args.n_samples:
                survived = completed - fell_count
                print(
                    f"[heron] {completed}/{args.n_samples}: "
                    f"survived={survived} ({survived / completed:.1%}), "
                    f"avg_dist={distance_sum / completed:+.3f}m, "
                    f"max_dist={distance_max:+.3f}m, "
                    f"max_flips={flips_max}, "
                    f"avg_wall={wall_sum / completed:.2f}s/sim"
                )
    finally:
        if args.n_procs > 1:
            pool.close()
            pool.join()

    total_wall = time.perf_counter() - t_total

    # Write samples.jsonl in original sample order for reproducibility.
    with samples_path.open("w") as out:
        for i in range(args.n_samples):
            if i in rows_by_i:
                out.write(json.dumps(rows_by_i[i]) + "\n")

    summary = {
        "n_samples": args.n_samples,
        "seed": args.seed,
        "seconds": args.seconds,
        "dt": args.dt,
        "n_procs": args.n_procs,
        "fell_count": fell_count,
        "survived_count": args.n_samples - fell_count,
        "survival_rate": (args.n_samples - fell_count) / args.n_samples,
        "distance_avg": distance_sum / args.n_samples,
        "distance_max": distance_max,
        "wall_seconds_avg_per_sim": wall_sum / args.n_samples,
        "wall_seconds_total_sim": wall_sum,
        "wall_seconds_total_run": total_wall,
        "speedup_vs_serial": (wall_sum / total_wall) if total_wall > 0 else 1.0,
        "max_stance_flips": flips_max,
    }
    summary_path = out_dir / "summary.json"
    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"[heron] summary -> {summary_path}")
    print(
        f"[heron] total wall = {total_wall:.1f}s, "
        f"sum of per-sim wall = {wall_sum:.1f}s, "
        f"speedup = {summary['speedup_vs_serial']:.2f}x"
    )


if __name__ == "__main__":
    main()
