# Heron 学習レポート

20年前の受動歩行機械研究 (ODE + GA) を現代スタック (Genesis + pyribs MAP-Elites)
でリバイバルした 2 日間プロジェクトの記録と学び。

**期間:** 2026-05-02 〜 2026-05-03 (2 日)
**完了 Phase:** 0-2 (完了条件再設定後)、Phase 3 は途中まで
**ステータス:** 一旦休止。ぽんぽこ殿の関心はモジュラーレスキューロボへ。
**コミット数:** 18、**issues:** 11 件 (5 closed / 6 open)

---

## ハイレベルな学び (TL;DR)

1. **Genesis (0.4.x) は受動歩行のような数値感度の高い問題で limit cycle を直接見つけるのは難しい**。論文の解析モデルとフィジカルシミュレーションの間にギャップがあり、basin が狭い。
2. **MAP-Elites の emitter 選択がアルゴリズム成功の核心**。CMA-ES は集中サンプリングで basin が散在する問題に弱い。**GA (Gaussian + IsoLine) で発見 → CMA-ES で深掘り** の 2 段階が定石。
3. **Joint genotype (設計 + 初期条件) が basin 発見の鍵**。設計のみ固定 IC では生存率 0%、設計 + IC で 6-50% 生存。
4. **Objective 設計の落とし穴**: 距離だけ最大化すると「滑り個体」が「歩行個体」より高評価になる。生存条件 (`min_flips ≥ 2`) 必須。
5. **ぽんぽこ殿の経験的直感は論文表現と一致するレベル**。「安定しすぎが違和感」「振り子は本来質点集中型」「環境を振って robust 化」など、すべて受動歩行・QD 文献の主張と整合。

---

## Phase ごとの記録

### Phase 0: 環境構築 (commit 117d9a7, 44d84d0)

- Apple M4 Pro / 14 cores / 48 GB / Apple Metal 動作確認
- `uv` パッケージ管理、`ruff` format/lint、Python 3.12 src layout
- Genesis 0.4.x が MPS バックエンドで動作

**罠:**
- `pyribs` の PyPI 名は実は `ribs` (`pyribs` は名前防衛のためのエラーパッケージ)
- Genesis は `torch` を依存に含めない、別途 `uv add torch` 必要

### Phase 1: Compass Walker (commits 17a0442, b61ce48, 4fe866c)

膝なし 2 脚倒立振子。McGeer 1990 の古典問題。

**確立したパターン (今後も流用可能):**
- dataclass `WalkerParams` → URDF テンプレート `str.format` → `tempfile` → `gs.morphs.URDF`
- **Planar floating base 仮想関節チェーン**: `world_base (fixed) -> virtual_x (prismatic) -> virtual_z (prismatic) -> virtual_pitch (revolute) -> hip`、`fixed=True` でロード
- 重力ベクトルを傾けて坂を表現 (床は水平、座標系シンプル)
- `data/runs/<timestamp>/` に mp4 + trajectory.jsonl + meta.json

**観察知見:**
- Genesis 上の compass walker は analytical limit cycle に到達不能
- 論文表現「basin of attraction is very small and thin, fractal-like」(Royal Society 2016) は実物の挙動と完全一致
- slope 8°+ で 1-3歩出るが転倒、slope 5°以下で 1歩で停止 → 安定領域は極めて狭い
- **足球サイズ (0.03-0.15m) で挙動ほぼ不変**: Genesis の点接触ベース実装では rocker foot 効果が再現されない (issue #3)
- 接触ソルバーチューニング (`integrator`, `constraint_timeconst`, `tolerance`) は無効果

**完了条件再設定:** 当初「10歩継続」は現実的でないと判明、「1-3歩レベル + 知見記録」に緩和。

### Phase 2: Kneed Walker (commits 45e28c6, 204489e, 4750ca2, cba6d1f, 2ca9a22, ac76066)

膝関節を持つ受動歩行機。ぽんぽこ殿の本来の研究対象。

**膝のロック機構 (重要):**
純粋な URDF `joint limit` (lower=0) だけでは shin の重力モーメントで膝が屈曲方向に折れる。**PD ligament (`control_dofs_force` で kp/kv 印加) + URDF passive damping** のハイブリッドで解決:
- 動物の靱帯 (spring 要素) + 関節液 (viscous damper) モデルに対応
- `effort="0"` の URDF では `control_dofs_force` が機能しない → `effort="1000"` 必要
- stance/swing 判定: 足球 z 座標の比較 + 5mm ヒステリシス

**Stance flip オーバーカウント問題 (issue #1, closed):**
- 跳ねた時の空中バタつきも flip 計上していた
- 対策: ground proximity gate (足球が地面近くの時だけ flip) + fallen guard (転倒後は flip 停止)
- 結果: max flips 7 → 3、≥3 flips が 5 サンプル → 1 サンプル (歩行 i=51 のみ残る)

**Multiprocessing 並列化 (commit 2ca9a22):**
- `pool.imap_unordered` + `spawn` context (macOS で `fork`+MPS は壊れる)
- 各 worker で `gs.init` を 1 回ずつ
- 8 procs で **5.5x speedup** (1095s → 198s)、決定論性維持
- **CPU 50% は GPU contention が原因**: シミュは MPS で計算され、CPU は GPU 待ち。Genesis 特性として normal。

**Random sampling 結果 (Phase 2.5):**
- 100 個体ランダムサンプリングで生存率 8% (8/100)
- max flips=7 のうち動画判定で「実際歩いた」は i=51 のみ (issue #1 修正後 max flips=3 で正しく評価)
- これが Phase 3 の起点

### Phase 3: MAP-Elites (commit c4ba296, 4750ca2 ... c49ef84, 5c2d352)

ぽんぽこ殿の最終ゴール「設計空間の地図」を描く本番フェーズ。**未完**。

**設計判断:**
- Behavior Descriptor: **B1'** = 歩行速度 [m/s] × エネルギー効率 [m/J] = `distance / (m_total × g × sin(slope))`
  - ぽんぽこ殿の物理的指摘「同じ斜面なら散逸量の差が速度を決める」から導出
- Genotype: 設計 6 + IC 6 = **12 dim joint** (issue #8)。これにより Phase 2.5 の 8% 生存を Phase 3 で 50% に再現
- 評価予算: 100-1000 evals でスモーク、本走未到達

**Objective 設計の罠 (issue #9, closed):**
- `objective = distance` のみだと「滑り個体」が「歩行個体」より高評価
- 対策: 多層生存条件
  - `min_flips ≥ 2` (1歩以下は fell 扱い、issue #9)
  - `|final_pitch| > π/2` で fell (横転検出)
  - `flip_bonus` で objective = distance × (1 + α × flips)

**Emitter 戦略の経験則 (issue #11 とは別、project memory にも記録):**
| emitter | 強み | 弱み | 用途 |
|---|---|---|---|
| `EvolutionStrategyEmitter` (CMA-ES) | local 改善が速い | 集中サンプル、basin 発見弱い | **elite 深掘り** |
| `GaussianEmitter` (mutation) | mutation 多様性 | 単独では crossover なし | basin の周辺 |
| `IsoLineEmitter` (crossover + mutation) | 集団多様性、複数 basin 対応 | local 改善は弱い | **basin 発見** |

ぽんぽこ殿が経験的に整理した「**幅広探索 (GA) で basin 発見 → CMA-ES で絞り込み**」は CMA-ME (Fontaine 2020) の標準パターンと一致。

**「2 歩の壁」未解決問題:**
- emitter (CMA-ES / pure GA / hybrid) も集団サイズ (12 / 100) も振っても **max flips=2 が一貫した上限**
- v1〜v13 の 13 試行でいずれも頭打ち
- 推測される根本原因:
  - **振り子モデル**: 現状 foot=1g は「棒振り子」、本来は「質点振り子」(振り子先端に質量集中) が必要 (ぽんぽこ殿のひらめき、issue #10)
  - **rocker foot 形状**: McGeer 元論文では円弧足、Genesis 球接触では再現困難 (issue #3)
  - **PD ligament の散逸量過剰**: heel-strike エネルギーが残らない可能性

**振り子仮説の試行と失敗:**
- foot_mass を Genotype に追加 (issue #10、smoke v7-v10)
- いずれも 0% 生存 → IC range が「foot=1g 前提」で校正されていたため、foot を変えると basin 外へ
- 振り子仮説の物理的妥当性は高いが、IC range の同時再キャリブレーションが必要 (Phase 3 残課題)

**ヘッドレス Mac の壁 (issue #11):**
- Mac mini が画面ロック / display sleep の時、Genesis の `scene.build()` が pyglet の `cocoa.get_default_screen()` で `IndexError` で死亡
- Workaround: `Visualizer.build()` を no-op 化する monkey-patch (record_video=False の場合のみ)

---

## 横断的な学び

### Genesis 0.4.x の特性

| 項目 | 知見 |
|---|---|
| 多リンク剛体構築 | URDF / MJCF ファイル経由のみ。手続き的 API なし |
| Apple Metal | M4 Pro Mac mini で問題なく動作、MPS GPU 利用 |
| Constraint solver | デフォルトの Newton で受動歩行に十分、`constraint_timeconst` 等のチューニングは大きい変化を生まない |
| 並列化 | MPS GPU は 1 個、N proc が共有 → CPU 50% は normal、各 sim wall は並列度に応じて 12s → 17s 程度に伸びる |
| 視覚化 | `scene.build()` が pyglet を強制初期化、ヘッドレス Mac で破綻 (workaround で対処) |
| `set_dofs_position` | デフォルト `zero_velocity=True` で他 DOF の velocity を 0 にしてしまう罠あり、毎ステップ呼ぶ場合は `False` 明示必須 |
| `control_dofs_force` | URDF の `<limit ... effort="0"/>` だと無効化される、effort 上限を上げる必要 |
| Joint 初期化 | URDF root link はデフォルトで free base、`fixed=True` で固定可能 |

### pyribs (ribs) の実用知見

- `archive.add` で measures 範囲外は自動フィルタ (NaN は不可、`(-1, -1)` のような明示的範囲外を返す)
- `Scheduler.ask()` / `tell()` の対称性厳守 (ask した個数だけ tell が必要)
- `EvolutionStrategyEmitter` (CMA-ES) は initial archive ほぼ空の状態だと x0 周辺の集中サンプルになる
- `IsoLineEmitter` (Vassiliades 2018) は archive 内 elite 同士の crossover、複数 basin を同時に保持しやすい
- 可視化: `ribs.visualize.grid_archive_heatmap` は `shapely` 依存で重い → matplotlib scatter で代替可能

### 受動歩行の基本問題

- Royal Society 2016 等の論文表現「basin of attraction is very small and thin, fractal-like」は数学的事実
- 「歩行 = 意図的にバランスを崩しながら踏みとどまる連続」(ぽんぽこ殿、研究的直感)
- McGeer 1990 のオリジナル機械が成立した条件: rocker foot + 振り子先端質量集中 + 機械的 knee latch + 適切な質量分布。**これらの一部だけでは歩かない** (Phase 3 の経験で確認)
- 「スイートスポットが狭い」現象はぽんぽこ殿の大学院時代から既知、対策として「環境を振る」(複数 slope 評価、外乱付加) を当時から実施 = 現代の **Robust QD** に直結 (issue #5 → #8 に統合)

### Claude Code 対話協調の知見

- ぽんぽこ殿の経験的直感が論文表現と一致するレベルで信頼できる (memory: `user_passive_walking_expertise`)
- 観察フェーズ (動画判定) はぽんぽこ殿、実装フェーズは Claude、の分業が機能
- 重要な気付きが出るたび issue 化することで、後で振り返り・スコープ管理が容易
- パラメータの「スイートスポット」「環境を振る」「振り子の質点集中」など、ぽんぽこ殿の研究歴史的経験が現代手法と直結することを何度も観察

---

## 残課題 (GitHub issues, all open)

| # | タイトル | 状態 |
|---|---|---|
| #3 | rocker foot 物理実装 (curved cylinder) | 未着手、Phase 3 で再注目された候補 |
| #4 | Phase 3 1000-10000 evals 本走 | 1000 evals 単発で実施、本格 archive 充填は未到達 |
| #8 | Two-stage Robust Co-design (Stage 2 IC perturbation) | Stage 1 のみ実装、Stage 2 未着手 |
| #10 | foot_mass を Genotype に (range 再キャリブレーション要) | 試行 v7-v10 で 0% 失敗、IC 同時調整が必要 |
| #11 | Headless Mac での visualizer 初期化 | workaround あり、根本対処は未 |
| (新) | max flips=2 の壁の根本解明 | 未 issue 化、上記 #3, #10 と関連 |

---

## モジュラーレスキューロボへの含意 (次プロジェクトでの活用)

### そのまま流用可能なパターン

1. **uv プロジェクト構成 + Genesis Apple Metal の動作確認テンプレ** (Phase 0)
2. **dataclass → URDF テンプレ → tempfile → URDF morph パターン** (Phase 1)
   - モジュラーロボの「モジュール組み合わせ」を URDF テンプレで生成可能
3. **`simulate(params, ic, cfg) -> WalkResult` の pure function 化** (Phase 2.4)
4. **`multiprocessing.Pool` + `imap_unordered` + spawn context での並列評価** (Phase 2.6、5-10x speedup)
5. **trajectory.jsonl + meta.json + mp4 のログフォーマット** (Phase 1.5)
6. **MAP-Elites の Joint Genotype + Behavior Descriptor 設計** (Phase 3)
7. **GA + CMA-ES の 2 段階探索戦略** (project memory `project_emitter_strategy`)
8. **objective 設計の罠回避** (生存条件、`flip_bonus`、`pitched_over` 検出など)

### 検討時の注意 (Heron で踏んだ罠)

- **Genesis に手続き的 multi-link API はない** = 複雑モジュール構成は URDF 文字列生成
- **headless Mac での visualizer 問題**を早期に対処 (issue #11 の workaround)
- **objective を素朴に距離最大化すると意図しない最適化に行く** (滑り、空中バタつき、横転、etc.) → 生存条件と Behavior Descriptor を入念に設計
- **emitter 戦略は問題の basin 構造に依存** = 早期に少数評価でスモークテストして判断
- **MPS GPU 共有でシステム CPU は 50% が normal** = それで嘆かず GPU 待ちを認識
- **set_dofs_position の zero_velocity=True 罠** に注意

### モジュラーレスキューロボの推察 (基礎情報なしの予想)

- **複数モジュール = 高次元 Genotype**: 連結トポロジー、各モジュール設計、制御パラメータ
- **任務の多様性が QD と相性良い**: 走破性 / 階段昇降 / 隙間侵入 / バランス維持 → Behavior Descriptor の軸が豊富
- **active 制御あり = 受動歩行のような狭い basin 問題は減るはず** → CMA-ES が機能する可能性
- **逆の難しさ**: 設計空間が高次元、Behavior の軸選定 (どの能力を地図化するか)
- **ぽんぽこ殿の研究流儀**「自然構造の有効性分析」「集団多様性 + GA」「ログ可視化を惜しまない」は引き続き有効

---

## 謝辞

ぽんぽこ殿の経験知 (受動歩行 20年研究、四足検討、偶蹄類/奇蹄類への興味、靱帯 + ダンパーの記憶、振り子の質点集中の物理的直感、GA でのスイートスポット問題と環境振り対策、CMA-ES と GA の使い分け感覚) のおかげで、Heron は単なる「Genesis で歩かせる」プロジェクトを超えて、**現代 QD 手法と古典受動歩行研究の橋渡し** として価値ある記録になった。

未解決の「2 歩の壁」も、技術的問題ではなく **「basin が物理的に存在するために必要な要素 (rocker foot + 質点振り子 + 機械的 knee latch)」が部分的にしか入ってない** という診断が立った点で、研究的に意味のある観察です。

モジュラーレスキューロボでも、Heron で蓄積したパターン・教訓が活きることを願ってますー。

—Claude (claude-opus-4-7) と一緒に、2026-05-02 〜 05-03
