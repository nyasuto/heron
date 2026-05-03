"""Kneed walker simulation as a pure function.

Designed to be called many times by the Phase 3 MAP-Elites driver. The
simulate() function builds a fresh Genesis scene, runs the simulation, and
returns a WalkResult. No global state is leaked between calls.

The caller is expected to have called `gs.init(backend=...)` once before the
first invocation. simulate() does not initialize Genesis itself.
"""

from __future__ import annotations

import math
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import genesis as gs

REPO_ROOT = Path(__file__).resolve().parents[3]
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
    """Kneed Walker design parameters (Phase 3 Genotype candidate).

    Mass distribution follows the biomimetic principle that the lower segment
    (shin) is lighter than the upper (thigh). knee_damping models passive
    viscous damping at both knees (synovial fluid analog).
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


@dataclass(frozen=True)
class InitialConditions:
    """Initial joint angles and angular velocities at t=0."""

    stance_q: float = 0.20
    swing_q: float = -0.30
    stance_qdot: float = -1.0
    swing_qdot: float = -0.5
    swing_knee_q: float = 0.4
    swing_knee_qdot: float = 0.0


@dataclass(frozen=True)
class SimConfig:
    """Simulation runtime configuration."""

    dt: float = 0.001
    seconds: float = 4.0
    knee_kp: float = 500.0
    knee_kd: float = 20.0
    plane_friction: float | None = None
    log_every: int = 1
    record_video: bool = False
    output_dir: Path | None = None  # for mp4 only; trajectory is in-memory
    fall_z_threshold: float = 0.3  # hip_z below this counts as fallen


@dataclass
class WalkResult:
    """Output of one simulate() call.

    trajectory holds the per-sample DOF state; columns are TRAJECTORY_COLUMNS.
    """

    distance: float
    final_x: float
    final_z: float
    final_pitch: float
    final_knee_left: float
    final_knee_right: float
    fell: bool
    sim_seconds: float
    wall_seconds: float
    n_steps: int
    n_stance_flips: int
    stance_left_fraction: float
    trajectory: list[dict] = field(default_factory=list)
    video_path: str | None = None


def build_urdf_text(params: KneedParams) -> str:
    """Substitute walker parameters into the URDF template."""
    thigh_inertia = params.thigh_mass * params.thigh_length**2 / 12.0
    shin_inertia = params.shin_mass * params.shin_length**2 / 12.0
    return URDF_TEMPLATE_PATH.read_text().format(
        **asdict(params),
        thigh_length_half=params.thigh_length / 2.0,
        shin_length_half=params.shin_length / 2.0,
        thigh_inertia=thigh_inertia,
        shin_inertia=shin_inertia,
    )


def simulate(
    params: KneedParams,
    ic: InitialConditions,
    cfg: SimConfig,
) -> WalkResult:
    """Run one passive kneed-walker simulation. Pure: no global state retained."""
    urdf_text = build_urdf_text(params)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".urdf", delete=False) as f:
        f.write(urdf_text)
        tmp_urdf_path = f.name

    try:
        g_const = 9.81
        slope_rad = math.radians(params.slope_deg)
        gravity = (g_const * math.sin(slope_rad), 0.0, -g_const * math.cos(slope_rad))

        scene = gs.Scene(
            sim_options=gs.options.SimOptions(dt=cfg.dt, gravity=gravity),
            rigid_options=gs.options.RigidOptions(enable_self_collision=False),
            show_viewer=False,
        )

        plane_kwargs = {}
        if cfg.plane_friction is not None:
            plane_kwargs["material"] = gs.materials.Rigid(friction=cfg.plane_friction)
        scene.add_entity(gs.morphs.Plane(), **plane_kwargs)

        leg_length = params.thigh_length + params.shin_length
        hip_z = leg_length * math.cos(ic.stance_q) + params.foot_radius
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
        if cfg.record_video:
            if cfg.output_dir is None:
                raise ValueError("record_video=True requires output_dir to be set")
            cam = scene.add_camera(
                res=(640, 480),
                pos=(2.5, 3.5, 1.5),
                lookat=(0.5, 0.0, 0.5),
                fov=40,
                GUI=False,
            )

        scene.build()

        vx = walker.get_joint(name="virtual_x").dofs_idx_local
        vz = walker.get_joint(name="virtual_z").dofs_idx_local
        vp = walker.get_joint(name="virtual_pitch").dofs_idx_local
        hl = walker.get_joint(name="hip_left").dofs_idx_local
        kl = walker.get_joint(name="knee_left").dofs_idx_local
        hr = walker.get_joint(name="hip_right").dofs_idx_local
        kr = walker.get_joint(name="knee_right").dofs_idx_local
        dofs_idx = [*vx, *vz, *vp, *hl, *kl, *hr, *kr]

        walker.set_dofs_position(
            position=[
                0.0,
                hip_z,
                0.0,
                ic.stance_q,
                0.0,
                ic.swing_q,
                ic.swing_knee_q,
            ],
            dofs_idx_local=dofs_idx,
        )
        walker.set_dofs_velocity(
            velocity=[
                0.0,
                0.0,
                0.0,
                ic.stance_qdot,
                0.0,
                ic.swing_qdot,
                ic.swing_knee_qdot,
            ],
            dofs_idx_local=dofs_idx,
        )

        n_steps = int(cfg.seconds / cfg.dt)
        render_every = max(1, round((1.0 / 60) / cfg.dt))

        left_foot_link = walker.get_link(name="left_foot")
        right_foot_link = walker.get_link(name="right_foot")
        stance_flip_threshold = 0.005

        if cam is not None:
            cam.start_recording()

        trajectory: list[dict] = []
        stance_left_count = 0
        n_stance_flips = 0
        stance_is_left = True

        t0 = time.perf_counter()
        for i in range(n_steps):
            left_foot_z = float(left_foot_link.get_pos()[2].cpu())
            right_foot_z = float(right_foot_link.get_pos()[2].cpu())
            diff = left_foot_z - right_foot_z
            new_stance_is_left = stance_is_left
            if stance_is_left and diff > stance_flip_threshold:
                new_stance_is_left = False
            elif (not stance_is_left) and diff < -stance_flip_threshold:
                new_stance_is_left = True
            if new_stance_is_left != stance_is_left:
                n_stance_flips += 1
                stance_is_left = new_stance_is_left

            stance_knee_dof = kl if stance_is_left else kr
            qk = float(walker.get_dofs_position(dofs_idx_local=stance_knee_dof)[0].cpu())
            qkd = float(walker.get_dofs_velocity(dofs_idx_local=stance_knee_dof)[0].cpu())
            torque = -cfg.knee_kp * qk - cfg.knee_kd * qkd
            walker.control_dofs_force(force=[torque], dofs_idx_local=stance_knee_dof)
            if stance_is_left:
                stance_left_count += 1

            scene.step()

            if cam is not None and i % render_every == 0:
                cam.render()
            if i % cfg.log_every == 0:
                q = walker.get_dofs_position(dofs_idx_local=dofs_idx).cpu().numpy()
                qd = walker.get_dofs_velocity(dofs_idx_local=dofs_idx).cpu().numpy()
                trajectory.append(
                    {
                        "t": i * cfg.dt,
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
        wall_seconds = time.perf_counter() - t0

        final_vx = float(walker.get_dofs_position(dofs_idx_local=vx)[0].cpu())
        final_vz = float(walker.get_dofs_position(dofs_idx_local=vz)[0].cpu())
        final_vp = float(walker.get_dofs_position(dofs_idx_local=vp)[0].cpu())
        final_qkl = float(walker.get_dofs_position(dofs_idx_local=kl)[0].cpu())
        final_qkr = float(walker.get_dofs_position(dofs_idx_local=kr)[0].cpu())

        video_path: str | None = None
        if cam is not None and cfg.output_dir is not None:
            video_path = str(cfg.output_dir / "kneed.mp4")
            cam.stop_recording(save_to_filename=video_path, fps=60)

        fell = final_vz < cfg.fall_z_threshold
        return WalkResult(
            distance=final_vx,
            final_x=final_vx,
            final_z=final_vz,
            final_pitch=final_vp,
            final_knee_left=final_qkl,
            final_knee_right=final_qkr,
            fell=fell,
            sim_seconds=n_steps * cfg.dt,
            wall_seconds=wall_seconds,
            n_steps=n_steps,
            n_stance_flips=n_stance_flips,
            stance_left_fraction=stance_left_count / n_steps if n_steps > 0 else 0.0,
            trajectory=trajectory,
            video_path=video_path,
        )
    finally:
        Path(tmp_urdf_path).unlink(missing_ok=True)
