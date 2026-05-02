"""Phase 1: Compass Gait Walker.

Tilted-gravity formulation: floor is horizontal, gravity vector is rotated by
slope_deg about the y-axis. Equivalent to a sloped floor with vertical gravity,
but keeps the kinematics simpler.

Initial (q, qdot) defaults are set near a Goswami-1998-style limit cycle for a
~3-degree slope. Walking from rest is essentially impossible; the initial joint
velocities are what put the system into the falling-and-catching pattern that is
walking.
"""

from __future__ import annotations

import argparse
import math
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

    Defaults follow the regime studied by Goswami et al. 1998: unit leg length,
    hip-heavy mass distribution.
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
    d = CompassParams()
    parser = argparse.ArgumentParser(description="Heron Phase 1 compass walker")
    parser.add_argument("--viewer", action="store_true", help="Interactive viewer instead of mp4")
    parser.add_argument("--seconds", type=float, default=2.0, help="Simulated duration")
    parser.add_argument("--dt", type=float, default=0.001, help="Physics timestep (1ms default)")

    g = parser.add_argument_group("walker design parameters")
    g.add_argument("--leg-length", type=float, default=d.leg_length, help="Leg length [m]")
    g.add_argument("--leg-radius", type=float, default=d.leg_radius, help="Leg cylinder radius [m]")
    g.add_argument("--leg-mass", type=float, default=d.leg_mass, help="Per-leg mass [kg]")
    g.add_argument("--hip-mass", type=float, default=d.hip_mass, help="Hip point mass [kg]")
    g.add_argument(
        "--foot-radius", type=float, default=d.foot_radius, help="Foot sphere radius [m]"
    )
    g.add_argument("--foot-mass", type=float, default=d.foot_mass, help="Foot sphere mass [kg]")
    g.add_argument("--slope-deg", type=float, default=d.slope_deg, help="Effective slope [deg]")

    e = parser.add_argument_group("environment")
    e.add_argument(
        "--plane-friction",
        type=float,
        default=None,
        help="Plane friction coefficient (Genesis default ~1.0 if unset; valid 0.01-5.0)",
    )

    i = parser.add_argument_group("initial conditions")
    i.add_argument("--stance-q", type=float, default=0.20, help="Initial stance-leg angle [rad]")
    i.add_argument("--swing-q", type=float, default=-0.30, help="Initial swing-leg angle [rad]")
    i.add_argument(
        "--stance-qdot", type=float, default=-1.0, help="Initial stance angular velocity [rad/s]"
    )
    i.add_argument(
        "--swing-qdot", type=float, default=-0.5, help="Initial swing angular velocity [rad/s]"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    params = CompassParams(
        leg_length=args.leg_length,
        leg_radius=args.leg_radius,
        leg_mass=args.leg_mass,
        hip_mass=args.hip_mass,
        foot_radius=args.foot_radius,
        foot_mass=args.foot_mass,
        slope_deg=args.slope_deg,
    )

    urdf_text = build_urdf_text(params)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".urdf", delete=False) as f:
        f.write(urdf_text)
        tmp_urdf_path = f.name

    gs.init(backend=gs.metal)
    print(f"[heron] backend = {gs.backend}, device = {gs.device}")
    print(f"[heron] params = {params}")

    g = 9.81
    slope_rad = math.radians(params.slope_deg)
    gravity = (g * math.sin(slope_rad), 0.0, -g * math.cos(slope_rad))
    print(f"[heron] gravity (tilted) = {gravity}")

    scene = gs.Scene(
        sim_options=gs.options.SimOptions(dt=args.dt, gravity=gravity),
        rigid_options=gs.options.RigidOptions(
            enable_self_collision=False,
        ),
        show_viewer=args.viewer,
    )

    plane_kwargs = {}
    if args.plane_friction is not None:
        plane_kwargs["material"] = gs.materials.Rigid(friction=args.plane_friction)
        print(f"[heron] plane friction = {args.plane_friction}")
    scene.add_entity(gs.morphs.Plane(), **plane_kwargs)

    # Hip height that puts the stance foot on the ground for the chosen stance angle.
    hip_z = params.leg_length * math.cos(args.stance_q) + params.foot_radius
    walker = scene.add_entity(
        gs.morphs.URDF(
            file=tmp_urdf_path,
            pos=(0.0, 0.0, 0.0),
            fixed=True,
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

    # 5 DOFs: virtual_x, virtual_z, virtual_pitch, hip_left, hip_right. The first three
    # form the planar floating base; the last two are the actual hip hinges.
    vx = walker.get_joint(name="virtual_x").dofs_idx_local
    vz = walker.get_joint(name="virtual_z").dofs_idx_local
    vp = walker.get_joint(name="virtual_pitch").dofs_idx_local
    hl = walker.get_joint(name="hip_left").dofs_idx_local
    hr = walker.get_joint(name="hip_right").dofs_idx_local
    dofs_idx = [*vx, *vz, *vp, *hl, *hr]

    walker.set_dofs_position(
        position=[0.0, hip_z, 0.0, args.stance_q, args.swing_q],
        dofs_idx_local=dofs_idx,
    )
    walker.set_dofs_velocity(
        velocity=[0.0, 0.0, 0.0, args.stance_qdot, args.swing_qdot],
        dofs_idx_local=dofs_idx,
    )
    print(
        f"[heron] init hip_z = {hip_z:.3f}, q = ({args.stance_q:+.3f}, {args.swing_q:+.3f}), "
        f"qdot = ({args.stance_qdot:+.3f}, {args.swing_qdot:+.3f})"
    )

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

    final_vx = walker.get_dofs_position(dofs_idx_local=vx).cpu().numpy()
    final_vz = walker.get_dofs_position(dofs_idx_local=vz).cpu().numpy()
    final_vp = walker.get_dofs_position(dofs_idx_local=vp).cpu().numpy()
    final_hip_link_pos = walker.get_link(name="hip").get_pos().cpu().numpy()
    print(
        f"[heron] final virtual: x={float(final_vx[0]):+.4f}m, "
        f"z={float(final_vz[0]):+.4f}m, pitch={float(final_vp[0]):+.4f}rad"
    )
    print(f"[heron] final hip link pos = {final_hip_link_pos}")

    if cam is not None:
        out_dir = Path("data/runs") / time.strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "compass.mp4"
        cam.stop_recording(save_to_filename=str(out_path), fps=60)
        print(f"[heron] video saved to {out_path}")

    Path(tmp_urdf_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
