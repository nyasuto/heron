# Genesis + 機械学習 ベストプラクティス調査

Heron での試行から見えた **CPU 50% 問題** を起点に、Genesis の正しい並列化パターンを
調査した結果。次プロジェクト (モジュラーレスキューロボ) の設計判断に直結。

**作成日:** 2026-05-03
**ベース実験:** Heron (Phase 0-3)、特に Phase 2.6 の multiprocessing 並列化
**比較ベンチ:** sample_kneed.py で MPS / CPU を切り替え

---

## TL;DR

1. **Genesis の正しい使い方は `scene.build(n_envs=N)` の batched envs**。Heron でやった
   「multiprocessing で 1 GPU を共有」は anti-pattern。
2. **batched envs は env ごとに質量・慣性を変えられる**。`set_mass(mass, envs_idx=...)`
   で MAP-Elites / 進化アルゴリズムにも対応可能。
3. **形状 (リンク長、半径) は build 時固定** = Genotype に形状を含めると process 並列が必要。
   この場合は **形状を外側、質量/IC を内側 (batched envs)** のハイブリッド構造が筋。
4. **CPU backend (Taichi CPU) は MPS より純計算速度は速い** 場合がある (Heron での実測 ~16x FPS、
   ただし 1 sim wall は compile overhead で同等)。**MPS が万能ではない**。
5. RL 例では `n_envs=4096` が標準。10000+ envs まで FPS が線形スケール。

---

## Genesis の並列化アーキテクチャ (公式ドキュメント引用)

### 1. Batched Environments (推奨)

```python
scene.build(n_envs=4096)  # 4096 並列シミュレーション

# 状態は batched で取得
positions = robot.get_qpos()  # shape (4096, n_dofs)

# 部分的に env を更新
robot.set_dofs_position(target, envs_idx=[0, 5, 10, ...])
```

- "4096 environments don't run sequentially. They share computation via **batched matrix
  operations**" (Genesis ドキュメント)
- "Total FPS scales **linearly** with the number of parallel environments **up to 32,768
  environments**" (公式ベンチマーク)
- 単一 GPU で完全並列、CPU-GPU 同期がない (= 真の "GPU-native")

### 2. Per-environment 設定変更

`scene.build(n_envs=N)` 後、env ごとに以下が変えられる：

| API | 変更可能な範囲 |
|---|---|
| `RigidLink.set_mass(mass, envs_idx)` | リンク質量 |
| `RigidEntity.set_links_inertial_mass(...)` | 慣性質量 |
| `RigidEntity.set_mass_shift(...)` | 重心位置 |
| `set_dofs_position(..., envs_idx)` | 初期姿勢 |
| `set_dofs_velocity(..., envs_idx)` | 初期角速度 |
| `set_dofs_kp / set_dofs_kv` | PD ゲイン (env ごと?) |

**build 時固定 (env ごと不可変):**
- リンク長、形状、URDF 構造
- ジョイント構成

### 3. Multi-GPU

- `multiprocessing` で別 GPU 割り当て (環境変数経由)
- もしくは `torch.distributed` + DDP + NCCL backend (PyTorch ML pipeline 統合時)

### 4. RL の標準パターン

公式 locomotion チュートリアル (Unitree Go2):
```python
scene = gs.Scene(...)
scene.add_entity(go2_urdf)
scene.add_entity(plane)
scene.build(n_envs=4096)  # 4096 並列学習環境

# 1 step で 4096 体の robot が同時に動く
for step in range(N):
    actions = policy(observations)  # (4096, action_dim)
    robot.set_dofs_position(actions, envs_idx=...)
    scene.step()
    observations, rewards = compute(robot.get_qpos())  # batched
```

---

## Heron でなぜ CPU 50% だったか

Heron の構成:
```
multiprocessing.Pool(processes=10)
  ├── Worker 1: gs.init(metal) → Scene 1 → simulate
  ├── Worker 2: gs.init(metal) → Scene 2 → simulate
  ├── ... (10 procs)
  └── Worker 10: gs.init(metal) → Scene 10 → simulate
```

**問題点:**
- 10 個の独立な Genesis インスタンスが **1 個の MPS GPU を共有**
- 各 worker は GPU dispatch → 結果待ち でブロック
- CPU 使用率 50% = GPU 計算待ちで CPU が idle
- これは Genesis の "no CPU-GPU bottleneck" の前提を完全に外した使い方

**正しい構成 (例):**
```
1 process で:
  scene = gs.Scene(...)
  scene.add_entity(walker_urdf)
  scene.build(n_envs=10000)  # 10000 並列
  
  for step in range(N):
      scene.step()  # 1 ステップで 10000 体が動く

  → GPU を batched 計算で full 使用、CPU 100%、FPS が線形スケール
```

---

## CPU vs MPS 性能比較 (Heron 実測)

### 比較条件
- M4 Pro (10 P-core + 4 E-core, 14 CPU + MPS)
- 同じ KneedParams, 50 サンプル, seconds=2.0
- workers は spawn context, 各 worker で gs.init

### 結果 (M4 Pro 実測)

| Bench | backend | n_procs | wall (s) | per-sim wall (s) | survived | 比 vs A |
|---|---|---|---|---|---|---|
| A | metal (MPS) | 10 | **66.2** | 12.61 | 5/50 | 1.0x |
| B | cpu (Taichi) | 1 | 40.2 | 0.80 | 5/50 | 1.6x |
| **C** | **cpu (Taichi)** | **10** | **9.8** | 1.36 | 5/50 | **6.8x** |

### 衝撃の知見

- **CPU x 10 procs が MPS x 10 procs より 6.8倍速い**
- CPU 単独 (B) でも MPS 10並列 (A) より速い (40s vs 66s)
- 結果 (生存率, max distance) は完全に同じ → 物理は同等、速度だけ違う

### 単発比較 (smoke, 1 sim 2 秒)

| backend | FPS (running) | 1 sim wall (含 compile) | 純計算速度 |
|---|---|---|---|
| metal (MPS) | ~308 | ~11s | 1x |
| **cpu (Taichi)** | **~4,800** (!!) | ~17s | **16x** |

**1 sim あたり計算 FPS が CPU の方が 16倍速い**。MPS は dispatch overhead が大きく、
1 体の単純 walker のような small sim では cost > benefit。

### MPS の真価はどこにあるか

Genesis の MPS バックエンドが本当に活きるのは：
- **`scene.build(n_envs=4096)` のような batched envs** (1 process で数千-数万体並列)
- batched matrix operations で GPU を full に使う設計
- RL 学習 (PPO 等) のような大規模並列ロールアウト

Heron 型の使い方 (multiprocess + 1 walker per process) では **MPS の overhead が逆に重荷**。

### Phase 3 への含意 (Heron 再開時)

| 構成 | 1000 evals 推定時間 |
|---|---|
| 現状: MPS x 10 procs | **23 分** (実測 198s × 10 sample 比例外挿で実測値そのもの) |
| **CPU x 10 procs に切替えるだけ** | **約 2.3 分** ← 10倍速 |
| batched envs (n_envs=1000) | 数秒〜数十秒 (10x-100x 想定) |

CPU backend に切り替えるだけで 10倍速 → これだけで Heron Phase 3 の 1000 evals が
1日かかる時間スケールから数分に。10000 evals (本格 archive) も 30 分で。

---

## 次プロジェクトへの含意

### 設計判断の指針

1. **シミュ単発 or バッチかを明確化**
   - 単発: backend は CPU でも MPS でも同等
   - バッチ (10+): MPS + n_envs が圧倒的有利

2. **Genotype を「形状」と「動的パラメータ」に分離**
   - 形状 (URDF 構造、リンク長): 外側 process 並列 (or 形状探索フェーズ別)
   - 質量、慣性、初期条件、PD ゲイン: 内側 batched envs

3. **進化アルゴリズム (MAP-Elites / GA) の構成例**
   ```
   for generation in range(N):
       solutions = ask()  # 例: (n_shapes, n_per_shape) = (32, 128)
       
       for shape in unique_shapes(solutions):  # 32 個の URDF
           scene = gs.Scene(...)
           scene.add_entity(URDF(shape))
           scene.build(n_envs=128)  # 質量/IC 違いの 128 体
           set_per_env_params(solutions[shape])
           run(steps)
           collect_results()
       
       tell(results)
   ```
   この構造で `multiprocessing` は形状単位でのみ使う (URDF build がボトルネック)。

4. **RL の場合は完全に batched envs**
   - `n_envs=4096-8192` を 1 process で
   - PPO 等の policy update は batched observation で
   - process 並列は不要 (単一 GPU で十分)

5. **計算資源を前提に設計**
   - Apple Silicon Mac mini = 単一 MPS、batched envs 限界 ~10000 envs (要 RAM 確認)
   - Multi-GPU 機 = `torch.distributed` + DDP
   - CPU-only 環境 = Taichi CPU backend、SIMD で十分速い

### Heron への適用 (再開する場合)

issue #4 (Phase 3 本走) を batched envs 構成に書き換える：
- 質量 (thigh/shin/hip/foot) は per-env 可変 → batched envs 内
- リンク長 (thigh/shin) は env 不可変 → process 並列
- IC (6 dim) は per-env 可変 (set_dofs_position の envs_idx) → batched envs 内

**ハイブリッド構成例:**
- 100 形状 × 100 質量+IC バリエーション = 10000 評価
- 10 process (各 10 形状) × 100 batched envs/process
- 推定: 現状 30 時間 → **30 分** に短縮 (60x speedup)

---

## 参考情報源

- [Genesis 公式ドキュメント](https://genesis-world.readthedocs.io/) — `scene.build(n_envs=N)`, `set_mass(envs_idx=...)` 等
- [Parallel Simulation チュートリアル](https://genesis-world.readthedocs.io/en/latest/user_guide/getting_started/parallel_simulation.html)
- [Multi-GPU Simulation](https://genesis-world.readthedocs.io/en/latest/user_guide/advanced_topics/multi_gpu.html)
- [Locomotion RL チュートリアル](https://genesis-world.readthedocs.io/en/latest/user_guide/getting_started/locomotion.html) — `n_envs=4096` の標準パターン
- [Genesis: 430,000x faster than real-time (Medium)](https://medium.com/@saimanideep.ch12345/genesis-training-robots-430-000x-faster-than-real-time-c6b6775eee63) — batched envs の威力
- [ManiSkill3 (比較対象)](https://arxiv.org/html/2410.00425v2) — GPU-parallelized robotics の他例
