"""Phase 1: Compass Gait Walker minimal prototype.

Drops a 2-link passive walker on a sloped plane. Fixed parameters from literature
(Goswami 1998 / McGeer 1990 conventions). No CSV logging yet — that lands in 1.5.
The goal of this iteration is just: URDF loads, base is floating, walker reacts to gravity.
"""

from __future__ import annotations

import argparse
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import genesis as gs

REPO_ROOT = Path(__file__).resolve().parent.parent
URDF_TEMPLATE_PATH = REPO_ROOT / "assets" / "compass.urdf.tmpl"


@dataclass(frozen=True)
class CompassParams:
    """Compass Gait Walker design parameters.

    Defaults are in the regime studied by Goswami et al. 1998 ('A Study of the Passive
    Gait of a Compass-Like Biped Robot'): unit leg length, hip-heavy mass distribution.
    """

    leg_length: float = 1.0
    leg_radius: float = 0.02
    leg_mass: float = 5.0
    hip_mass: float = 10.0
    foot_radius: float = 0.03
    foot_mass: float = 1e-3
    slope_deg: float = 3.0


def build_urdf_text(params: CompassParams) -> str:
    """Substitute walker parameters into the URDF template."""
    leg_inertia = params.leg_mass * params.leg_length**2 / 12.0
    return URDF_TEMPLATE_PATH.read_text().format(
        **asdict(params),
        leg_length_half=params.leg_length / 2.0,
        leg_inertia=leg_inertia,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Heron Phase 1 compass walker (1.2 prototype)")
    parser.add_argument("--viewer", action="store_true", help="Interactive viewer instead of mp4")
    parser.add_argument("--seconds", type=float, default=2.0, help="Simulated duration")
    parser.add_argument("--dt", type=float, default=0.001, help="Physics timestep (1ms default)")
    parser.add_argument(
        "--swing-init",
        type=float,
        default=0.3,
        help="Initial swing-leg angle in radians (right leg forward = positive)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    params = CompassParams()

    urdf_text = build_urdf_text(params)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".urdf", delete=False) as f:
        f.write(urdf_text)
        tmp_urdf_path = f.name

    gs.init(backend=gs.metal)
    print(f"[heron] backend = {gs.backend}, device = {gs.device}")
    print(f"[heron] params = {params}")

    scene = gs.Scene(
        sim_options=gs.options.SimOptions(dt=args.dt, gravity=(0.0, 0.0, -9.81)),
        show_viewer=args.viewer,
    )

    # Sloped ground: rotate the plane about y so the +x direction goes downhill.
    scene.add_entity(gs.morphs.Plane(euler=(0.0, params.slope_deg, 0.0)))

    walker = scene.add_entity(
        gs.morphs.URDF(
            file=tmp_urdf_path,
            pos=(0.0, 0.0, params.leg_length + params.foot_radius + 0.02),
            fixed=False,
            default_armature=0.0,
            requires_jac_and_IK=False,
            merge_fixed_links=True,
            links_to_keep=("left_foot", "right_foot"),
        ),
    )

    cam = None
    if not args.viewer:
        cam = scene.add_camera(
            res=(640, 480),
            pos=(2.5, 3.5, 1.5),
            lookat=(0.5, 0.0, 0.5),
            fov=40,
            GUI=False,
        )

    scene.build()

    # The entity has 8 DOFs total: 6 for the floating base + 2 hip hinges. Address the
    # hip DOFs by joint name so we don't accidentally set base coordinates here.
    hip_left_dof = walker.get_joint(name="hip_left").dofs_idx_local
    hip_right_dof = walker.get_joint(name="hip_right").dofs_idx_local
    init_left = -args.swing_init * 0.3
    init_right = args.swing_init
    walker.set_dofs_position(
        position=[init_left, init_right],
        dofs_idx_local=[*hip_left_dof, *hip_right_dof],
    )
    print(f"[heron] initial joint angles: hip_left={init_left:.3f}, hip_right={init_right:.3f}")

    n_steps = int(args.seconds / args.dt)
    render_every = max(1, round((1.0 / 60) / args.dt))

    if cam is not None:
        cam.start_recording()

    t0 = time.perf_counter()
    for i in range(n_steps):
        scene.step()
        if cam is not None and i % render_every == 0:
            cam.render()
    elapsed = time.perf_counter() - t0
    sim_seconds = n_steps * args.dt
    print(f"[heron] simulated {sim_seconds:.2f}s ({n_steps} steps) in {elapsed:.2f}s wall-clock")

    final_hip_pos = walker.get_pos()
    print(f"[heron] final hip position = {final_hip_pos}")

    if cam is not None:
        out_dir = Path("data/runs") / time.strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "compass.mp4"
        cam.stop_recording(save_to_filename=str(out_path), fps=60)
        print(f"[heron] video saved to {out_path}")

    Path(tmp_urdf_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
