"""Phase 2: Kneed Walker CLI wrapper.

Thin command-line wrapper around heron.walker.kneed.simulate(). Handles
argparse, gs.init, and writing trajectory.jsonl + meta.json next to the mp4.
The simulation logic itself lives in src/heron/walker/kneed.py so the Phase 3
MAP-Elites driver can import simulate() directly.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import genesis as gs

from heron.walker.kneed import (
    TRAJECTORY_COLUMNS,
    InitialConditions,
    KneedParams,
    SimConfig,
    simulate,
)


def parse_args() -> argparse.Namespace:
    p = KneedParams()
    ic = InitialConditions()
    cfg = SimConfig()
    parser = argparse.ArgumentParser(description="Heron Phase 2 kneed walker")
    parser.add_argument("--seconds", type=float, default=cfg.seconds)
    parser.add_argument("--dt", type=float, default=cfg.dt)
    parser.add_argument("--log-every", type=int, default=cfg.log_every)
    parser.add_argument("--no-video", action="store_true", help="Skip mp4 recording")

    g = parser.add_argument_group("walker design parameters")
    g.add_argument("--thigh-length", type=float, default=p.thigh_length)
    g.add_argument("--shin-length", type=float, default=p.shin_length)
    g.add_argument("--thigh-radius", type=float, default=p.thigh_radius)
    g.add_argument("--shin-radius", type=float, default=p.shin_radius)
    g.add_argument("--thigh-mass", type=float, default=p.thigh_mass)
    g.add_argument("--shin-mass", type=float, default=p.shin_mass)
    g.add_argument("--hip-mass", type=float, default=p.hip_mass)
    g.add_argument("--foot-radius", type=float, default=p.foot_radius)
    g.add_argument("--foot-mass", type=float, default=p.foot_mass)
    g.add_argument("--knee-limit-upper", type=float, default=p.knee_limit_upper)
    g.add_argument("--knee-damping", type=float, default=p.knee_damping)
    g.add_argument("--slope-deg", type=float, default=p.slope_deg)

    e = parser.add_argument_group("environment")
    e.add_argument("--plane-friction", type=float, default=cfg.plane_friction)

    k = parser.add_argument_group("knee latch (PD ligament on stance knee)")
    k.add_argument("--knee-kp", type=float, default=cfg.knee_kp)
    k.add_argument("--knee-kd", type=float, default=cfg.knee_kd)

    i = parser.add_argument_group("initial conditions")
    i.add_argument("--stance-q", type=float, default=ic.stance_q)
    i.add_argument("--swing-q", type=float, default=ic.swing_q)
    i.add_argument("--stance-qdot", type=float, default=ic.stance_qdot)
    i.add_argument("--swing-qdot", type=float, default=ic.swing_qdot)
    i.add_argument("--swing-knee-q", type=float, default=ic.swing_knee_q)
    i.add_argument("--swing-knee-qdot", type=float, default=ic.swing_knee_qdot)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    params = KneedParams(
        thigh_length=args.thigh_length,
        shin_length=args.shin_length,
        thigh_radius=args.thigh_radius,
        shin_radius=args.shin_radius,
        thigh_mass=args.thigh_mass,
        shin_mass=args.shin_mass,
        hip_mass=args.hip_mass,
        foot_radius=args.foot_radius,
        foot_mass=args.foot_mass,
        knee_limit_upper=args.knee_limit_upper,
        knee_damping=args.knee_damping,
        slope_deg=args.slope_deg,
    )
    ic = InitialConditions(
        stance_q=args.stance_q,
        swing_q=args.swing_q,
        stance_qdot=args.stance_qdot,
        swing_qdot=args.swing_qdot,
        swing_knee_q=args.swing_knee_q,
        swing_knee_qdot=args.swing_knee_qdot,
    )

    out_dir = Path("data/runs") / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = SimConfig(
        dt=args.dt,
        seconds=args.seconds,
        knee_kp=args.knee_kp,
        knee_kd=args.knee_kd,
        plane_friction=args.plane_friction,
        log_every=args.log_every,
        record_video=not args.no_video,
        output_dir=out_dir,
    )

    gs.init(backend=gs.metal)
    print(f"[heron] backend = {gs.backend}, device = {gs.device}")
    print(f"[heron] params = {params}")
    print(f"[heron] ic = {ic}")
    print(f"[heron] output dir = {out_dir}")

    result = simulate(params, ic, cfg)

    print(
        f"[heron] simulated {result.sim_seconds:.2f}s ({result.n_steps} steps) "
        f"in {result.wall_seconds:.2f}s wall-clock"
    )
    print(
        f"[heron] final: x={result.final_x:+.4f}m, z={result.final_z:+.4f}m, "
        f"pitch={result.final_pitch:+.4f}rad, fell={result.fell}"
    )
    print(
        f"[heron] knees: left={result.final_knee_left:+.4f}rad, "
        f"right={result.final_knee_right:+.4f}rad"
    )
    print(f"[heron] stance left {result.stance_left_fraction:.1%} ({result.n_stance_flips} flips)")
    if result.video_path is not None:
        print(f"[heron] video saved to {result.video_path}")

    traj_path = out_dir / "trajectory.jsonl"
    with traj_path.open("w") as f:
        for row in result.trajectory:
            f.write(json.dumps(row) + "\n")
    print(f"[heron] trajectory: {len(result.trajectory)} samples -> {traj_path}")

    meta = {
        "phase": 2,
        "model": "kneed_walker",
        "params": asdict(params),
        "initial_conditions": asdict(ic),
        "sim": {
            "dt": cfg.dt,
            "seconds": cfg.seconds,
            "n_steps": result.n_steps,
            "log_every": cfg.log_every,
            "knee_kp": cfg.knee_kp,
            "knee_kd": cfg.knee_kd,
            "plane_friction": cfg.plane_friction,
            "rigid_options": {"enable_self_collision": False},
        },
        "result": {
            "wall_seconds": result.wall_seconds,
            "distance": result.distance,
            "final_x": result.final_x,
            "final_z": result.final_z,
            "final_pitch": result.final_pitch,
            "final_knee_left": result.final_knee_left,
            "final_knee_right": result.final_knee_right,
            "fell": result.fell,
            "n_stance_flips": result.n_stance_flips,
            "stance_left_fraction": result.stance_left_fraction,
        },
        "trajectory_columns": list(TRAJECTORY_COLUMNS),
        "video": Path(result.video_path).name if result.video_path else None,
    }
    meta_path = out_dir / "meta.json"
    with meta_path.open("w") as f:
        json.dump(meta, f, indent=2)
    print(f"[heron] meta -> {meta_path}")


if __name__ == "__main__":
    main()
