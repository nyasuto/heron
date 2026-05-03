"""Phase 3 Stage 1: joint MAP-Elites exploration of kneed walker (design + IC).

See issue #8 (Two-stage Robust Co-design). Stage 1 explores 12 dims jointly to
find the basin of attraction; Stage 2 (separate script, future) will perturb IC
around discovered elites to find robust designs.

Behavior Descriptor (B1', see GOALS_NEXT.md):
  - x: average walking speed [m/s] = distance / sim_seconds
  - y: energy efficiency [m/J] = distance / (m_total * g * sin(slope))

Genotype (12 dims, normalized to [0, 1]):
  Design (6): thigh_length, shin_length, thigh_mass, shin_mass,
              hip_mass, knee_damping
  IC      (6): stance_q, swing_q, stance_qdot, swing_qdot,
              swing_knee_q, swing_knee_qdot
  (foot_mass / foot_radius are held fixed at KneedParams defaults; the
   pendulum hypothesis from issue #10 will be re-tested after pure-GA
   baselines confirm survival behavior.)

Usage:
    # 2 emitters x batch 5 = 10 evals/iter (matches default --n-procs 10)
    uv run python scripts/mapelites_kneed.py --iterations 100 --batch-size 5
    uv run python scripts/mapelites_kneed.py --iterations 1000 --batch-size 5 --n-procs 10
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import time
from dataclasses import asdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless

import matplotlib.pyplot as plt
import numpy as np
from ribs.archives import GridArchive
from ribs.emitters import EvolutionStrategyEmitter, GaussianEmitter, IsoLineEmitter
from ribs.schedulers import Scheduler

from heron.walker.kneed import (
    InitialConditions,
    KneedParams,
    SimConfig,
    simulate,
)

# Stage 1 joint Genotype: 6 design dims + 6 IC dims = 12 dims (see issue #8).
# Design dims (0-5) match Phase 2.5 sample_params ranges; IC dims (6-11) match
# Phase 2.5 sample_ic ranges (those are the conditions where 8% survived).
# (foot_mass / foot_radius held fixed at KneedParams defaults pending issue #10
# re-test; pure-GA baseline first to isolate the emitter effect from the
# foot-pendulum hypothesis.)
GENOTYPE_NAMES = (
    "thigh_length", "shin_length", "thigh_mass", "shin_mass",
    "hip_mass", "knee_damping",
    "stance_q", "swing_q", "stance_qdot", "swing_qdot",
    "swing_knee_q", "swing_knee_qdot",
)  # fmt: skip
# 12-dim Genotype (foot_mass / foot_radius temporarily reverted to fixed values
# to isolate the effect of pure-GA emitters from the foot-pendulum hypothesis;
# see issue #10 for the foot-mass exploration to be reintroduced after baseline).
GENOTYPE_LOWS = np.array([0.3, 0.3, 1.5, 1.0, 5.0, 0.1, 0.10, -0.40, -2.0, -1.5, 0.0, -1.0])
GENOTYPE_HIGHS = np.array([0.7, 0.7, 4.0, 3.5, 20.0, 1.0, 0.30, -0.15, -0.5, 0.5, 0.6, 0.5])
SOLUTION_DIM = len(GENOTYPE_NAMES)

# Behavior Descriptor ranges (initial guess; adjust if archive saturates one wall)
BD_RANGES = [(0.0, 1.5), (0.0, 1.5)]  # speed [m/s], efficiency [m/J]
BD_DIMS = [20, 20]

GRAVITY = 9.81


def normalize_to_params_and_ic(
    x: np.ndarray, slope_deg: float
) -> tuple[KneedParams, InitialConditions]:
    """Convert a normalized [0, 1]^12 vector to (KneedParams, InitialConditions)."""
    clipped = np.clip(x, 0.0, 1.0)
    actual = GENOTYPE_LOWS + clipped * (GENOTYPE_HIGHS - GENOTYPE_LOWS)
    params = KneedParams(
        thigh_length=float(actual[0]),
        shin_length=float(actual[1]),
        thigh_mass=float(actual[2]),
        shin_mass=float(actual[3]),
        hip_mass=float(actual[4]),
        knee_damping=float(actual[5]),
        slope_deg=slope_deg,
    )
    ic = InitialConditions(
        stance_q=float(actual[6]),
        swing_q=float(actual[7]),
        stance_qdot=float(actual[8]),
        swing_qdot=float(actual[9]),
        swing_knee_q=float(actual[10]),
        swing_knee_qdot=float(actual[11]),
    )
    return params, ic


def total_mass(p: KneedParams) -> float:
    return p.hip_mass + 2 * (p.thigh_mass + p.shin_mass + p.foot_mass)


def evaluate_one(
    args: tuple[int, np.ndarray, float, float, float, float, int],
) -> tuple[int, float, float, float, dict]:
    """Run simulate() for one joint Genotype. Returns (idx, objective, speed, efficiency, info).

    Survival is conditional on actually walking: result is treated as fallen if any of
      - simulate's hip_z fall detection triggers
      - |final_pitch| exceeds max_pitch_rad (covers horizontal slipping)
      - n_stance_flips < min_flips (covers "took 0-1 steps then slid forever")
    The min_flips gate is the dominant fix from issue #9 — without it CMA-ES kept
    converging on long-distance slipping individuals over actual walkers.

    All simulate() exceptions are caught and reported as fell=True so a single
    bad parameter combination doesn't take down the worker pool.
    """
    idx, x, slope_deg, seconds, flip_bonus, max_pitch_rad, min_flips = args
    try:
        params, ic = normalize_to_params_and_ic(x, slope_deg)
        cfg = SimConfig(
            dt=0.001,
            seconds=seconds,
            record_video=False,
            record_trajectory=False,
        )
        result = simulate(params, ic, cfg)
    except Exception as e:
        return (idx, -1.0, -1.0, -1.0, {"fell": True, "error": type(e).__name__})

    pitched_over = abs(result.final_pitch) > max_pitch_rad
    insufficient_flips = result.n_stance_flips < min_flips
    fell = result.fell or pitched_over or insufficient_flips

    if fell:
        # Out-of-range measures so pyribs filters this out of the archive.
        return (
            idx,
            -1.0,
            -1.0,
            -1.0,
            {
                "fell": True,
                "pitched_over": pitched_over,
                "insufficient_flips": insufficient_flips,
                "distance": result.distance,
                "n_stance_flips": result.n_stance_flips,
            },
        )

    # Mild flip bonus on top of the survival gate; same distance, more flips => elite.
    base_distance = result.distance
    objective = base_distance * (1.0 + flip_bonus * result.n_stance_flips)

    # Behavior Descriptor uses raw distance (no flip bonus), so the archive cells
    # still represent (speed, efficiency) in physical units.
    speed = base_distance / result.sim_seconds
    energy_input = total_mass(params) * GRAVITY * math.sin(math.radians(slope_deg))
    efficiency = base_distance / energy_input if energy_input > 0 else 0.0
    info = {
        "fell": False,
        "pitched_over": False,
        "distance": base_distance,
        "n_stance_flips": result.n_stance_flips,
        "wall_seconds": result.wall_seconds,
        "final_pitch": result.final_pitch,
    }
    return (idx, objective, speed, efficiency, info)


def _worker_init(backend_name: str = "cpu") -> None:
    """Initialize Genesis once per worker.

    backend_name: 'cpu' (default for sampling, much faster on M4 Pro than MPS
    for single-walker per process — see GENESIS_BEST_PRACTICES.md), 'metal',
    or 'gpu'. Use 'metal' only if you are also using batched envs.

    Headless workaround: monkey-patch Visualizer.build() to a no-op so a
    detached display (locked screen, no monitor on the Mac mini, etc.) does
    not break scene.build() with `IndexError: list index out of range` from
    pyglet's cocoa.get_default_screen(). Safe here because evaluate_one only
    runs with record_video=False — workers never call cam.render().
    """
    import genesis as gs
    import genesis.vis.visualizer

    def _noop_build(self) -> None:
        self._is_built = True

    genesis.vis.visualizer.Visualizer.build = _noop_build  # type: ignore[assignment]
    backend = {"cpu": gs.cpu, "metal": gs.metal, "gpu": gs.gpu}[backend_name]
    gs.init(backend=backend)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Heron Phase 3 MAP-Elites for kneed walker")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Per-emitter batch size. Default 4 × 3 emitters (gaussian + ES + iso-line) "
        "= 12 evals/iter; with --n-procs 10 you'll see slight pool-idle but the GA "
        "diversity wins for basin discovery.",
    )
    parser.add_argument("--n-emitters", type=int, default=1, help="EvolutionStrategy emitters")
    parser.add_argument(
        "--n-iso-line",
        type=int,
        default=1,
        help="IsoLineEmitter count (Vassiliades 2018 GA-style: crossover + mutation)",
    )
    parser.add_argument(
        "--sigma0", type=float, default=0.30, help="Initial sigma for Gaussian + ES emitters"
    )
    parser.add_argument(
        "--iso-sigma",
        type=float,
        default=0.05,
        help="Iso-line emitter mutation sigma (default 0.05 = ~1.5%% per dim)",
    )
    parser.add_argument(
        "--line-sigma",
        type=float,
        default=0.20,
        help="Iso-line emitter crossover blend sigma (default 0.20 from the paper)",
    )
    parser.add_argument(
        "--no-gaussian",
        action="store_true",
        help="Disable the broad-sampling GaussianEmitter",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--slope-deg", type=float, default=3.0)
    parser.add_argument(
        "--flip-bonus",
        type=float,
        default=1.0,
        help="Objective multiplier per stance flip: obj = dist * (1 + flip_bonus * flips)",
    )
    parser.add_argument(
        "--max-pitch-rad",
        type=float,
        default=math.pi / 2,
        help="Walker is treated as fallen if |final pitch| exceeds this (default pi/2)",
    )
    parser.add_argument(
        "--min-flips",
        type=int,
        default=2,
        help="Minimum n_stance_flips required for an individual to be considered alive. "
        "Below this, the run is treated as fallen so the archive only fills with walkers.",
    )
    parser.add_argument(
        "--n-procs",
        type=int,
        default=10,
        help="Worker processes (default 10 = M4 Pro performance cores); 1 = sequential in-process",
    )
    parser.add_argument(
        "--backend",
        choices=["cpu", "metal", "gpu"],
        default="cpu",
        help="Genesis backend. Default 'cpu' is ~6.8x faster than 'metal' for the "
        "Heron-style multiprocess + single-walker pattern (see GENESIS_BEST_PRACTICES.md). "
        "Use 'metal' only if you also adopt batched envs (scene.build(n_envs=N)).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    out_dir = Path("data/runs") / (time.strftime("%Y%m%d_%H%M%S") + "_mapelites")
    out_dir.mkdir(parents=True, exist_ok=True)

    archive = GridArchive(
        solution_dim=SOLUTION_DIM,
        dims=BD_DIMS,
        ranges=BD_RANGES,
        seed=args.seed,
    )
    x0_center = np.full(SOLUTION_DIM, 0.5)  # center of [0, 1]^6
    sol_lower = np.zeros(SOLUTION_DIM)
    sol_upper = np.ones(SOLUTION_DIM)

    emitters: list = []
    if not args.no_gaussian:
        emitters.append(
            GaussianEmitter(
                archive,
                sigma=args.sigma0,
                x0=x0_center,
                lower_bounds=sol_lower,
                upper_bounds=sol_upper,
                batch_size=args.batch_size,
                seed=args.seed,
            )
        )
    for i in range(args.n_emitters):
        emitters.append(
            EvolutionStrategyEmitter(
                archive,
                x0=x0_center,
                sigma0=args.sigma0,
                batch_size=args.batch_size,
                seed=args.seed + 100 + i,
            )
        )
    for i in range(args.n_iso_line):
        emitters.append(
            IsoLineEmitter(
                archive,
                iso_sigma=args.iso_sigma,
                line_sigma=args.line_sigma,
                x0=x0_center,
                lower_bounds=sol_lower,
                upper_bounds=sol_upper,
                batch_size=args.batch_size,
                seed=args.seed + 200 + i,
            )
        )
    scheduler = Scheduler(archive, emitters)

    print(f"[heron] MAP-Elites: dim={SOLUTION_DIM}, archive={BD_DIMS}, ranges={BD_RANGES}")
    print(
        f"[heron] emitters: {len(emitters)} (gaussian={not args.no_gaussian}, "
        f"es={args.n_emitters}, iso-line={args.n_iso_line}) x batch_size {args.batch_size} "
        f"= {len(emitters) * args.batch_size} evals/iter"
    )
    print(f"[heron] target {args.iterations} iters, {args.n_procs} procs, output -> {out_dir}")

    if args.n_procs <= 1:
        _worker_init(args.backend)
        pool = None
    else:
        ctx = mp.get_context("spawn")
        pool = ctx.Pool(
            processes=args.n_procs,
            initializer=_worker_init,
            initargs=(args.backend,),
        )

    eval_log: list[dict] = []
    fell_count = 0
    survived_count = 0
    t_total = time.perf_counter()

    try:
        for itr in range(args.iterations):
            solutions = scheduler.ask()
            tasks = [
                (
                    i,
                    sol,
                    args.slope_deg,
                    args.seconds,
                    args.flip_bonus,
                    args.max_pitch_rad,
                    args.min_flips,
                )
                for i, sol in enumerate(solutions)
            ]

            # imap_unordered: workers pull next task as soon as they're free.
            # Results come back unordered; we re-sort by idx before telling pyribs.
            n = len(tasks)
            results_buf: list[tuple | None] = [None] * n
            if pool is None:
                for t in tasks:
                    r = evaluate_one(t)
                    results_buf[r[0]] = r
            else:
                for r in pool.imap_unordered(evaluate_one, tasks):
                    results_buf[r[0]] = r
            results = results_buf  # type: ignore[assignment]

            objectives = np.array([r[1] for r in results])  # type: ignore[index]
            measures = np.array([[r[2], r[3]] for r in results])  # type: ignore[index]
            scheduler.tell(objectives, measures)

            for sol, r in zip(solutions, results, strict=True):
                _, obj, speed, eff, info = r  # type: ignore[misc]
                if info["fell"]:
                    fell_count += 1
                else:
                    survived_count += 1
                params_log, ic_log = normalize_to_params_and_ic(sol, args.slope_deg)
                eval_log.append(
                    {
                        "iter": itr,
                        "solution": [float(v) for v in sol],
                        "params": asdict(params_log),
                        "ic": asdict(ic_log),
                        "objective": float(obj) if not info["fell"] else None,
                        "speed": float(speed) if not info["fell"] else None,
                        "efficiency": float(eff) if not info["fell"] else None,
                        **info,
                    }
                )

            # Per-iter log for the first 5 iterations, then every 10 iterations.
            verbose = itr < 5 or (itr + 1) % 10 == 0 or itr == args.iterations - 1
            if verbose:
                stats = archive.stats
                obj_max_str = f"{stats.obj_max:.3f}" if stats.num_elites > 0 else "n/a"
                print(
                    f"[heron] iter {itr + 1}/{args.iterations}: "
                    f"archive={stats.num_elites}/{BD_DIMS[0] * BD_DIMS[1]} "
                    f"({stats.coverage * 100:.1f}%) "
                    f"qd_score={stats.qd_score:.2f} "
                    f"obj_max={obj_max_str} "
                    f"survived={survived_count}/{survived_count + fell_count}"
                )
    finally:
        if pool is not None:
            pool.close()
            pool.join()

    total_wall = time.perf_counter() - t_total

    # Save archive contents
    df = archive.data(return_type="pandas")
    archive_path = out_dir / "archive.csv"
    df.to_csv(archive_path, index=False)
    print(f"[heron] archive: {len(df)} elites -> {archive_path}")

    # Save full per-evaluation log
    log_path = out_dir / "evals.jsonl"
    with log_path.open("w") as f:
        for row in eval_log:
            f.write(json.dumps(row) + "\n")
    print(f"[heron] eval log: {len(eval_log)} rows -> {log_path}")

    # Save summary
    summary = {
        "iterations": args.iterations,
        "batch_size": args.batch_size,
        "n_emitters": args.n_emitters,
        "sigma0": args.sigma0,
        "seed": args.seed,
        "seconds": args.seconds,
        "slope_deg": args.slope_deg,
        "n_procs": args.n_procs,
        "total_evals": len(eval_log),
        "survived": survived_count,
        "fell": fell_count,
        "survival_rate": survived_count / (survived_count + fell_count) if eval_log else 0.0,
        "archive_size": int(archive.stats.num_elites),
        "archive_capacity": BD_DIMS[0] * BD_DIMS[1],
        "coverage": float(archive.stats.coverage),
        "qd_score": float(archive.stats.qd_score),
        "obj_max": float(archive.stats.obj_max) if archive.stats.num_elites > 0 else None,
        "wall_seconds_total": total_wall,
        "wall_seconds_per_eval": total_wall / len(eval_log) if eval_log else 0.0,
    }
    with (out_dir / "summary.json").open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"[heron] total wall = {total_wall:.1f}s, summary -> {out_dir / 'summary.json'}")

    # Archive heatmap: scatter elites on the (speed, efficiency) plane,
    # colored by objective. Avoids ribs.visualize (which needs shapely) so we
    # don't pull an extra dependency.
    if archive.stats.num_elites > 0:
        speeds = df["measures_0"].to_numpy()
        effs = df["measures_1"].to_numpy()
        objs = df["objective"].to_numpy()

        fig, ax = plt.subplots(figsize=(8, 6))
        # Imshow-style cell background
        ax.set_xlim(BD_RANGES[0])
        ax.set_ylim(BD_RANGES[1])
        ax.set_xticks(np.linspace(BD_RANGES[0][0], BD_RANGES[0][1], 6))
        ax.set_yticks(np.linspace(BD_RANGES[1][0], BD_RANGES[1][1], 6))
        ax.grid(True, alpha=0.3)
        sc = ax.scatter(
            speeds,
            effs,
            c=objs,
            s=80,
            cmap="viridis",
            edgecolors="black",
            linewidths=0.4,
        )
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label("objective (distance) [m]")
        ax.set_xlabel("walking speed [m/s]")
        ax.set_ylabel("energy efficiency [m/J]")
        ax.set_title(
            f"Heron Phase 3 Stage 1 archive ({archive.stats.num_elites} elites, "
            f"{archive.stats.coverage * 100:.1f}% coverage)"
        )
        fig.tight_layout()
        heatmap_path = out_dir / "archive_heatmap.png"
        fig.savefig(heatmap_path, dpi=120)
        plt.close(fig)
        print(f"[heron] heatmap -> {heatmap_path}")
    else:
        print("[heron] archive empty, skipping heatmap")


if __name__ == "__main__":
    main()
