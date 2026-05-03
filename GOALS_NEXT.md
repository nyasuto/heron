# ゴール設定 (Phase 3 以降)

GOALS.md の Phase 0-2 に続く詳細。Phase 2 完了時 (2026-05-03) に作成。
Phase 0-2 は完了したので、これ以降の設計判断はこのファイルで管理する。

---

## Phase 3: MAP-Elites による設計空間探索

**目的:** Heron 全体の最終ゴールである「設計空間の地図」を実現する核心フェーズ。
受動歩行機械の設計空間を MAP-Elites で探索し、「どんな形が、どんな効率と
速度で歩けるか」の地図を可視化する。

20年前の研究では「最強の1個」しか得られなかった景色を、Quality Diversity
で「設計空間の全体像」として描き直す。

### 完了条件

- [ ] pyribs (`ribs` パッケージ) で MAP-Elites アーカイブを構築できる
- [ ] `KneedParams` ↔ Genotype vector の双方向変換が動く
- [ ] Behavior Descriptor (歩行速度 × エネルギー効率) が trajectory から計算できる
- [ ] 100/1000 サンプル評価でアーカイブが埋まっていく挙動が確認できる
- [ ] アーカイブを matplotlib heatmap で可視化できる
- [ ] アーカイブを `data/runs/<ts>_mapelites/archive.*` 等に保存できる
- [ ] 1000 サンプル評価が並列 (Phase 2.6 の multiprocessing) で 1時間以内に終わる

### アプローチ: 2段階 Robust Co-design (issue #8)

Phase 3 smoke test (480 evals 全部 fell) と Phase 2.5 知見 (IC 含めて
random sampling すれば 8% 生存) を踏まえ、2段階に再設計：

- **Stage 1**: 設計 6次元 + IC 6次元 = 12次元の joint genotype で basin を発見
- **Stage 2**: Stage 1 で見つかった設計の周辺で IC を Gaussian noise で揺らし、
  robust な個体を絞る (Cully 2015 系 Robust QD)

これにより以下 issue を統合的に解決:
- ~~#5 (Robust evaluation)~~: Stage 2 がこれそのもの
- ~~#7 (DEFAULT_IC fix)~~: Stage 1 で IC も探索するため不要

### Stage 1 Genotype (12 次元)

設計 6 + IC 6 を joint で探索。各 [0, 1] 正規化、評価時に実値範囲へ denormalize。

| フィールド | 範囲 | 単位 | 種別 |
|---|---|---|---|
| thigh_length | 0.3 - 0.7 | m | 設計 |
| shin_length | 0.3 - 0.7 | m | 設計 |
| thigh_mass | 1.5 - 4.0 | kg | 設計 |
| shin_mass | 1.0 - 3.5 | kg | 設計 |
| hip_mass | 5.0 - 20.0 | kg | 設計 |
| knee_damping | 0.1 - 1.0 | N⋅m⋅s/rad | 設計 |
| stance_q | 0.10 - 0.30 | rad | IC |
| swing_q | -0.40 - -0.15 | rad | IC |
| stance_qdot | -2.0 - -0.5 | rad/s | IC |
| swing_qdot | -1.5 - 0.5 | rad/s | IC |
| swing_knee_q | 0.0 - 0.6 | rad | IC |
| swing_knee_qdot | -1.0 - 0.5 | rad/s | IC |

固定値:
- `foot_radius`: 0.03 m (issue #3 の rocker foot 実装後に別途検討)
- `foot_mass`: 1e-3 kg
- `knee_limit_upper`: 2.5 rad
- `slope_deg`: 3.0 (環境定数として固定、変えると エネルギー効率の意味が変わるため)

### Stage 2 (Stage 1 完了後に詳細決定)

Stage 1 アーカイブから「elite 設計」の集合を抽出。各 elite に対して：
- 設計を固定
- IC を Stage 1 で見つかった IC を中心に Gaussian noise で N 回振る
- objective = 生存率 × 平均距離 (or min distance over IC samples)
- 新たな archive に robust 個体を蓄積

### Behavior Descriptor (アーカイブ軸)

**B1' (Phase 3 採用):**
- x 軸: **平均歩行速度** [m/s] = `distance / sim_seconds`
- y 軸: **エネルギー効率** = `distance / (m_total × g × sin(slope))`
  - 物理的意味: 重力ポテンシャル降下 1ジュールあたりの前進距離 [m/J 相当]
  - 同じ斜面では分母が一定なので、distance に比例。スケール定数として意味付け。

選定理由 (2026-05-03 ぽんぽこ殿の指摘より):
> 「同じ斜面で全て同じエネルギーが入る = 速度差はエネルギー散逸の差から生まれる」

エネルギー散逸の主因 (設計依存):
- heel-strike 衝撃 (歩幅 × 衝突角度に依存)
- knee_damping の viscous 散逸
- PD ligament の kd 項
- 慣性モーメント (脚長 × 質量分布) → 1サイクル時間 → 平均速度

これらが地図上に「形状ごとの効率」として現れる、ぽんぽこ殿の研究目的
「自然構造の有効性分析」に直結する Behavior Descriptor。

### 評価関数 (Stage 1)

- 12 次元 Genotype を `(KneedParams, InitialConditions)` に denormalize
- `simulate(params, ic, cfg)` を呼んで `WalkResult` を取得
- `fell=True` の場合は measures を範囲外 `(-1, -1)` で報告 → archive 投入されない
- objective: `distance` (前進距離が大きいほど良い、QD 的に各 cell の elite を更新)

### 評価予算

- **初期: 1000 回** で動作確認
- 感触見て段階的に 10000 回まで上げる候補
- M4 Pro 8 procs で 1サンプル ~17s → 1000 サンプル ~36分 ✓

### このフェーズで決めること

- archive grid 解像度 (例: 20×20、要ぽんぽこ殿合意)
- emitter の数・種類 (pyribs の `EvolutionStrategyEmitter` 等)
- 並列評価の実装パターン (Phase 2.6 の multiprocessing 流用)

### 段階的サブタスク

| # | 作業 | 状態 |
|---|---|---|
| 3.1-3.5 | mapelites_kneed.py (Scheduler / Emitter / Archive / 並列評価) 最小実装 | 完了 |
| 3.6 | アーカイブ可視化 (matplotlib heatmap) | 完了 (scatter plot) |
| 3.7 | Stage 1 段階的評価 (30 → 100 → 10000 evals) | **完了 (v18 main run)** |
| 3.8 | Stage 2 (IC perturbation) 実装 | 未着手、issue #8 残課題 |

### Phase 3 最終結果 (v18 main run, 2026-05-03)

| 指標 | 値 |
|---|---|
| 総評価数 | 10,000 |
| wall clock | 1477s (約 24分) |
| 生存率 | 12.1% (1207/10000) |
| archive elites | 27 (6.8% coverage) |
| qd_score | 214.75 |
| obj_max | 18.30 |
| **max stance flips** | **5** |
| **最終ベスト個体** | iter=32: **flips=5, distance 2.86m, 歩幅 0.57m/歩** (ぽんぽこ殿動画判定で「本物の歩行」) |

**ベスト個体パラメータ** (`assets/best_walker_phase3_v18_iter32.json`):
- thigh_length 0.70m, shin_length 0.40m (脚比 1.75:1)
- thigh_mass 2.67kg, shin_mass 2.03kg (脛が軽い、生物模倣)
- hip_mass 19.4kg (Genotype range の上限近く、hip-heavy)
- knee_damping 0.51

**punctuated equilibrium** (4 段階の jump):
- iter 4: 0 → 4.95 (basin 1)
- iter 20: 4.95 → 9.95 (basin 2)
- iter 40: 9.95 → 17.15 (basin 3)
- iter 100: 17.15 → 18.30 + max flips=5 突破 (basin 4)

### 動画判定で見えた未解決課題 (issue #13)

v18 top 3 elites (全て flips=5) のうち：
- **iter=32**: 本物の歩行 (歩幅 0.57m, 自然なリズム)
- iter=71: バネ前進
- iter=96: バネ前進 (距離 3.05m と最大だが歩行ではない)

数値のみでは「歩行」と「バネ」を区別不能。issue #13 で objective + 物理側の対策案を記録。
Heron はここで一旦完結、次プロジェクトで類似問題に当たった時に参照。

### 注意点

- Phase 2 の `simulate()` を変更しない (Phase 3 評価関数として呼ぶだけ)
- multiprocessing で各 worker が `gs.init` を 1回ずつ
- アーカイブ可視化は pyribs の標準ヘルパーがあるはず、調査して使う
- issue #1 (stance flip 修正済) の `n_stance_flips` も result から
  取得できるので、副次的な分析軸として trajectory dump に含める

---

## Phase 4 以降

未定。候補:
- 可視化ダッシュボード (Go + WebGL、別リポジトリで切り出し)
- POET 等 open-ended 探索手法
- Co-design (形状 × 制御の同時最適化)

これらは Phase 3 完了時に再検討する。
