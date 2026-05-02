"""Phase 0: drop a sphere onto a plane.

Default: headless, write mp4 to data/runs/<timestamp>/fall.mp4.
With --viewer: launch interactive viewer (no recording).
Apple Metal backend is requested explicitly; the startup banner shows what was actually selected.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import genesis as gs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Heron Phase 0 fall test")
    parser.add_argument(
        "--viewer",
        action="store_true",
        help="Show interactive viewer instead of recording an mp4",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=2.0,
        help="Simulated wall-clock duration",
    )
    parser.add_argument(
        "--dt",
        type=float,
        default=0.005,
        help="Physics timestep in seconds",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=60,
        help="Output video frames per second",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    gs.init(backend=gs.metal)
    print(f"[heron] genesis backend = {gs.backend}, device = {gs.device}")

    scene = gs.Scene(
        sim_options=gs.options.SimOptions(
            dt=args.dt,
            gravity=(0.0, 0.0, -9.81),
        ),
        show_viewer=args.viewer,
    )

    scene.add_entity(gs.morphs.Plane())
    scene.add_entity(gs.morphs.Sphere(pos=(0.0, 0.0, 1.0), radius=0.1))

    cam = None
    if not args.viewer:
        cam = scene.add_camera(
            res=(640, 480),
            pos=(2.5, 2.5, 1.5),
            lookat=(0.0, 0.0, 0.3),
            fov=40,
            GUI=False,
        )

    scene.build()

    n_steps = int(args.seconds / args.dt)
    render_every = max(1, round((1.0 / args.fps) / args.dt))

    if cam is not None:
        cam.start_recording()

    t0 = time.perf_counter()
    for i in range(n_steps):
        scene.step()
        if cam is not None and i % render_every == 0:
            cam.render()
    elapsed = time.perf_counter() - t0
    print(f"[heron] simulated {args.seconds:.2f}s ({n_steps} steps) in {elapsed:.2f}s wall-clock")

    if cam is not None:
        out_dir = Path("data/runs") / time.strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "fall.mp4"
        cam.stop_recording(save_to_filename=str(out_path), fps=args.fps)
        print(f"[heron] video saved to {out_path}")


if __name__ == "__main__":
    main()
