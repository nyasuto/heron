"""Phase 2: Kneed Walker.

Adds a knee joint with a McGeer-style mechanical stop (URDF revolute joint with
limit lower=0 to prevent over-extension). Reuses the planar virtual-joint base
established in Phase 1, and the same trajectory.jsonl + meta.json logging.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import genesis as gs

REPO_ROOT = Path(__file__).resolve().parent.parent
URDF_TEMPLATE_PATH = REPO_ROOT / "assets" / "kneed.urdf.tmpl"

TRAJECTORY_COLUMNS = (
    "t",
    "vx",
    "vz",
    "vp",
    "qhl",
    "qkl",
    "qhr",
    "qkr",
    "vxd",
    "vzd",
    "vpd",
    "qhld",
    "qkld",
    "qhrd",
    "qkrd",
)


@dataclass(frozen=True)
class KneedParams:
    """Kneed Walker design parameters.

    Mass distribution follows the biomimetic principle that the lower segment
    (shin) is lighter than the upper (thigh): less inertia at the distal end
    speeds up the swing.

    knee_damping: passive viscous damping at both knees (acts like joint synovial fluid).
    """

    thigh_length: float = 0.5
    shin_length: float = 0.5
    thigh_radius: float = 0.02
    shin_radius: float = 0.02
    thigh_mass: float = 2.5
    shin_mass: float = 2.0
    hip_mass: float = 10.0
    foot_radius: float = 0.03
    foot_mass: float = 1e-3
    knee_limit_upper: float = 2.5
    knee_damping: float = 0.5
    slope_deg: float = 3.0


def build_urdf_text(params: KneedParams) -> str:
    """Substitute kneed walker parameters into the URDF template."""
    thigh_inertia = params.thigh_mass * params.thigh_length**2 / 12.0
    shin_inertia = params.shin_mass * params.shin_length**2 / 12.0
    return URDF_TEMPLATE_PATH.read_text().format(
        **asdict(params),
        thigh_length_half=params.thigh_length / 2.0,
        shin_length_half=params.shin_length / 2.0,
        thigh_inertia=thigh_inertia,
        shin_inertia=shin_inertia,
    )


def parse_args() -> argparse.Namespace:
    d = KneedParams()
    parser = argparse.ArgumentParser(description="Heron Phase 2 kneed walker")
    parser.add_argument("--viewer", action="store_true", help="Interactive viewer instead of mp4")
    parser.add_argument("--seconds", type=float, default=2.0, help="Simulated duration")
    parser.add_argument("--dt", type=float, default=0.001, help="Physics timestep (1ms default)")
    parser.add_argument(
        "--log-every",
        type=int,
        default=1,
        help="Sample trajectory every N physics steps (1 = every step)",
    )

    g = parser.add_argument_group("walker design parameters")
    g.add_argument("--thigh-length", type=float, default=d.thigh_length)
    g.add_argument("--shin-length", type=float, default=d.shin_length)
    g.add_argument("--thigh-radius", type=float, default=d.thigh_radius)
    g.add_argument("--shin-radius", type=float, default=d.shin_radius)
    g.add_argument("--thigh-mass", type=float, default=d.thigh_mass)
    g.add_argument("--shin-mass", type=float, default=d.shin_mass)
    g.add_argument("--hip-mass", type=float, default=d.hip_mass)
    g.add_argument("--foot-radius", type=float, default=d.foot_radius)
    g.add_argument("--foot-mass", type=float, default=d.foot_mass)
    g.add_argument("--knee-limit-upper", type=float, default=d.knee_limit_upper)
    g.add_argument(
        "--knee-damping",
        type=float,
        default=d.knee_damping,
        help="Passive viscous damping at both knees (URDF dynamics damping)",
    )
    g.add_argument("--slope-deg", type=float, default=d.slope_deg)

    k = parser.add_argument_group("knee latch (PD ligament on stance knee)")
    k.add_argument(
        "--knee-kp", type=float, default=500.0, help="Stance knee PD position gain [N*m/rad]"
    )
    k.add_argument(
        "--knee-kd", type=float, default=20.0, help="Stance knee PD velocity gain [N*m/(rad/s)]"
    )

    e = parser.add_argument_group("environment")
    e.add_argument(
        "--plane-friction",
        type=float,
        default=None,
        help="Plane friction coefficient (Genesis default ~1.0 if unset)",
    )

    i = parser.add_argument_group("initial conditions")
    i.add_argument("--stance-q", type=float, default=0.20, help="Stance hip angle [rad]")
    i.add_argument("--swing-q", type=float, default=-0.30, help="Swing hip angle [rad]")
    i.add_argument("--stance-qdot", type=float, default=-1.0, help="Stance hip velocity [rad/s]")
    i.add_argument("--swing-qdot", type=float, default=-0.5, help="Swing hip velocity [rad/s]")
    i.add_argument(
        "--swing-knee-q", type=float, default=0.4, help="Swing knee angle [rad] (flexion positive)"
    )
    i.add_argument("--swing-knee-qdot", type=float, default=0.0, help="Swing knee velocity [rad/s]")
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

    # Hip height that puts the stance foot on the ground when the stance leg is fully
    # extended (knee = 0) at the stance hip angle.
    leg_length = params.thigh_length + params.shin_length
    hip_z = leg_length * math.cos(args.stance_q) + params.foot_radius
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

    # 7 DOFs: virtual_x, virtual_z, virtual_pitch, hip_left, knee_left, hip_right, knee_right.
    vx = walker.get_joint(name="virtual_x").dofs_idx_local
    vz = walker.get_joint(name="virtual_z").dofs_idx_local
    vp = walker.get_joint(name="virtual_pitch").dofs_idx_local
    hl = walker.get_joint(name="hip_left").dofs_idx_local
    kl = walker.get_joint(name="knee_left").dofs_idx_local
    hr = walker.get_joint(name="hip_right").dofs_idx_local
    kr = walker.get_joint(name="knee_right").dofs_idx_local
    dofs_idx = [*vx, *vz, *vp, *hl, *kl, *hr, *kr]

    # Stance leg: knee fully extended (0). Swing leg: knee flexed by swing_knee_q.
    walker.set_dofs_position(
        position=[
            0.0,
            hip_z,
            0.0,
            args.stance_q,
            0.0,
            args.swing_q,
            args.swing_knee_q,
        ],
        dofs_idx_local=dofs_idx,
    )
    walker.set_dofs_velocity(
        velocity=[
            0.0,
            0.0,
            0.0,
            args.stance_qdot,
            0.0,
            args.swing_qdot,
            args.swing_knee_qdot,
        ],
        dofs_idx_local=dofs_idx,
    )
    print(
        f"[heron] init hip_z = {hip_z:.3f}, "
        f"stance (q,qdot) = ({args.stance_q:+.3f}, {args.stance_qdot:+.3f}), "
        f"swing (q,qdot) = ({args.swing_q:+.3f}, {args.swing_qdot:+.3f}), "
        f"swing knee (q,qdot) = ({args.swing_knee_q:+.3f}, {args.swing_knee_qdot:+.3f})"
    )

    n_steps = int(args.seconds / args.dt)
    render_every = max(1, round((1.0 / 60) / args.dt))

    out_dir = Path("data/runs") / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[heron] output dir = {out_dir}")

    if cam is not None:
        cam.start_recording()

    trajectory: list[dict] = []
    left_foot_link = walker.get_link(name="left_foot")
    right_foot_link = walker.get_link(name="right_foot")

    # Hysteresis threshold for stance assignment: only flip stance when the
    # foot-height difference is clearly larger than this. Prevents per-step flapping.
    stance_flip_threshold = 0.005  # 5 mm

    t0 = time.perf_counter()
    stance_left_count = 0
    stance_is_left = True  # initial: left is stance
    for i in range(n_steps):
        # PD ligament on the stance knee: pull knee angle toward 0. Acts like the
        # collateral/cruciate ligaments that prevent stance-leg knee buckling. The
        # passive URDF damping on both knees stays in effect regardless.
        left_foot_z = float(left_foot_link.get_pos()[2].cpu())
        right_foot_z = float(right_foot_link.get_pos()[2].cpu())
        diff = left_foot_z - right_foot_z
        if stance_is_left and diff > stance_flip_threshold:
            stance_is_left = False
        elif (not stance_is_left) and diff < -stance_flip_threshold:
            stance_is_left = True
        stance_knee_dof = kl if stance_is_left else kr
        qk = float(walker.get_dofs_position(dofs_idx_local=stance_knee_dof)[0].cpu())
        qkd = float(walker.get_dofs_velocity(dofs_idx_local=stance_knee_dof)[0].cpu())
        torque = -args.knee_kp * qk - args.knee_kd * qkd
        walker.control_dofs_force(force=[torque], dofs_idx_local=stance_knee_dof)
        if stance_is_left:
            stance_left_count += 1

        scene.step()

        if cam is not None and i % render_every == 0:
            cam.render()
        if i % args.log_every == 0:
            q = walker.get_dofs_position(dofs_idx_local=dofs_idx).cpu().numpy()
            qd = walker.get_dofs_velocity(dofs_idx_local=dofs_idx).cpu().numpy()
            trajectory.append(
                {
                    "t": i * args.dt,
                    "vx": float(q[0]),
                    "vz": float(q[1]),
                    "vp": float(q[2]),
                    "qhl": float(q[3]),
                    "qkl": float(q[4]),
                    "qhr": float(q[5]),
                    "qkr": float(q[6]),
                    "vxd": float(qd[0]),
                    "vzd": float(qd[1]),
                    "vpd": float(qd[2]),
                    "qhld": float(qd[3]),
                    "qkld": float(qd[4]),
                    "qhrd": float(qd[5]),
                    "qkrd": float(qd[6]),
                }
            )
    elapsed = time.perf_counter() - t0
    sim_seconds = n_steps * args.dt
    stance_left_frac = stance_left_count / n_steps if n_steps > 0 else 0.0
    print(f"[heron] simulated {sim_seconds:.2f}s ({n_steps} steps) in {elapsed:.2f}s wall-clock")
    print(f"[heron] stance was left {stance_left_frac:.1%} of the time")

    final_vx = walker.get_dofs_position(dofs_idx_local=vx).cpu().numpy()
    final_vz = walker.get_dofs_position(dofs_idx_local=vz).cpu().numpy()
    final_vp = walker.get_dofs_position(dofs_idx_local=vp).cpu().numpy()
    final_qkl = walker.get_dofs_position(dofs_idx_local=kl).cpu().numpy()
    final_qkr = walker.get_dofs_position(dofs_idx_local=kr).cpu().numpy()
    final_hip_link_pos = walker.get_link(name="hip").get_pos().cpu().numpy()
    print(
        f"[heron] final virtual: x={float(final_vx[0]):+.4f}m, "
        f"z={float(final_vz[0]):+.4f}m, pitch={float(final_vp[0]):+.4f}rad"
    )
    print(
        f"[heron] final knee: left={float(final_qkl[0]):+.4f}rad, "
        f"right={float(final_qkr[0]):+.4f}rad"
    )
    print(f"[heron] final hip link pos = {final_hip_link_pos}")

    video_rel_path: str | None = None
    if cam is not None:
        video_path = out_dir / "kneed.mp4"
        cam.stop_recording(save_to_filename=str(video_path), fps=60)
        video_rel_path = video_path.name
        print(f"[heron] video saved to {video_path}")

    traj_path = out_dir / "trajectory.jsonl"
    with traj_path.open("w") as f:
        for row in trajectory:
            f.write(json.dumps(row) + "\n")
    print(f"[heron] trajectory: {len(trajectory)} samples -> {traj_path}")

    meta = {
        "phase": 2,
        "model": "kneed_walker",
        "params": asdict(params),
        "initial_conditions": {
            "stance_q": args.stance_q,
            "swing_q": args.swing_q,
            "stance_qdot": args.stance_qdot,
            "swing_qdot": args.swing_qdot,
            "swing_knee_q": args.swing_knee_q,
            "swing_knee_qdot": args.swing_knee_qdot,
            "hip_z": hip_z,
        },
        "sim": {
            "dt": args.dt,
            "seconds": args.seconds,
            "n_steps": n_steps,
            "log_every": args.log_every,
            "gravity": list(gravity),
            "plane_friction": args.plane_friction,
            "rigid_options": {"enable_self_collision": False},
        },
        "result": {
            "wall_seconds": elapsed,
            "final_virtual_x": float(final_vx[0]),
            "final_virtual_z": float(final_vz[0]),
            "final_virtual_pitch": float(final_vp[0]),
            "final_knee_left": float(final_qkl[0]),
            "final_knee_right": float(final_qkr[0]),
            "final_hip_link_pos": [float(v) for v in final_hip_link_pos],
        },
        "trajectory_columns": list(TRAJECTORY_COLUMNS),
        "video": video_rel_path,
    }
    meta_path = out_dir / "meta.json"
    with meta_path.open("w") as f:
        json.dump(meta, f, indent=2)
    print(f"[heron] meta -> {meta_path}")

    Path(tmp_urdf_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
