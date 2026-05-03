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

### Genotype (探索対象)

`KneedParams` のうち以下 **6 フィールド** を Genotype として振る：

| フィールド | 範囲 | 単位 |
|---|---|---|
| thigh_length | 0.3 - 0.7 | m |
| shin_length | 0.3 - 0.7 | m |
| thigh_mass | 1.5 - 4.0 | kg |
| shin_mass | 1.0 - 3.5 | kg |
| hip_mass | 5.0 - 20.0 | kg |
| knee_damping | 0.1 - 1.0 | N⋅m⋅s/rad |

固定値:
- `foot_radius`: 0.03 m (issue #3 の rocker foot 実装後に別途検討)
- `foot_mass`: 1e-3 kg
- `knee_limit_upper`: 2.5 rad
- `slope_deg`: 3.0 (環境定数として固定、変えると エネルギー効率の意味が変わるため)

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

### 評価関数

- Initial conditions は **固定** (現状 defaults: stance_q=0.20 等)
  - Phase 3 のスコープでは形状 (Genotype) 探索に集中、IC 探索は将来別途
- `simulate(params, ic_default, cfg)` を呼んで `WalkResult` を取得
- `fell=True` の場合は archive 投入をスキップ (生存個体のみ蓄積)
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

| # | 作業 |
|---|---|
| 3.1 | pyribs (`ribs`) の API 確認 + 最小サンプルで動作確認 |
| 3.2 | KneedParams ↔ Genotype vector wrapper |
| 3.3 | Behavior Descriptor 計算関数 (歩行速度 × エネルギー効率) |
| 3.4 | Scheduler / Emitter / Archive のセットアップ |
| 3.5 | 並列評価ループ (Phase 2.6 流用) |
| 3.6 | アーカイブ可視化 (matplotlib heatmap) |
| 3.7 | 段階的評価 (100 → 1000) |

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
