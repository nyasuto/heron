# ゴール設定

各フェーズの完了条件を明示する。完了条件を満たさないうちに次フェーズに進まない。

このドキュメントが扱うのは Phase 0 〜 2。Phase 3 以降は別途 `GOALS_NEXT.md` で扱う（Phase 2が終わるまで先送りでよい）。

---

## プロジェクト全体の最終ゴール

受動歩行機械の設計パラメータ空間を MAP-Elites で探索し、「どんな形・どんな歩き方の組み合わせが可能か」の地図を可視化する。

20年前の研究では「最強の1個」しか得られなかった景色を、Quality Diversity で「設計空間の全体像」として描き直す。

---

## Phase 0: 環境構築

**目的:** Genesisの感触を掴む。

### 完了条件

- [ ] M4 Mac mini上で Genesis 0.4.x が Apple Metal で動く
- [ ] uv でプロジェクトが管理されている (`uv sync` が通る)
- [ ] 「球を地面に落とす」最小デモが動画 or インタラクティブ表示で確認できる
- [ ] pyribs がインストールできる（実使用は Phase 3）
- [ ] `ruff format` / `ruff check` が通る空のテンプレートがある

### 注意点

- M4 Mac で Apple Metal が選ばれているか、起動ログで明示的に確認する
- Genesis のインストールでビルドエラーが出ることがある。その場合は Genesis の Issue を当たる

### このフェーズで決めること

- 動画出力 vs ライブビュー、どちらをデフォルトにするか
- Genesis のシーン構築 API のうち、どのスタイル（low-level / 高level wrapper）で書くか

---

## Phase 1: Compass Gait Walker

**目的:** 「物理が合っている」ことを確認する。古典問題なので失敗パターンが既知。

膝なしの2脚倒立振子モデル。最も単純な受動歩行機。McGeer (1990) で解析的に扱われている古典。

### 完了条件（再設定 2026-05-03）

当初「10歩継続」を完了条件としていたが、観察フェーズで Genesis 上の compass walker は
論文表現「basin of attraction is very small and thin, fractal-like」が現実の挙動として
確認できた。Genesis のフルフィジカルシミュレーションと論文の解析モデルとのギャップを
埋める追加実装は Phase 1 のスコープに収まらないと判断し、完了条件を以下に再設定する。

- [x] 膝なし2脚倒立振子モデルが、緩い斜面で前進する初期条件が複数見つかる（1-3歩レベル）
- [x] パラメータ（脚長、質量、足の半径、坂角度）を CLI で変えられる
- [ ] 歩行軌跡（重心位置、関節角度の時系列）をログに出力できる
- [x] Phase 1 で得た観察知見を残す（このセクションの末尾参照）

### このフェーズで決まったこと

- **ロボット記述方法**: URDF テンプレート (`assets/compass.urdf.tmpl`) + 仮想関節
  (virtual_x prismatic / virtual_z prismatic / virtual_pitch revolute) で planar floating base を実装。
  Genesis に手続き的 multi-link API がないための妥協。脚は cylinder + 球状足。
- **重力定式化**: 重力ベクトルを傾斜方向に回転（床は水平）。座標系が単純化される。
- **状態ログのフォーマット**: JSONL（DESIGN.md 通り、Phase 1.5 で実装）

### 観察知見（Phase 1 で確認された事実）

- **横転モードの拘束が必須**: 3D free base では y軸方向に最初に転倒する。仮想関節で planar
  拘束（x-z 平面に強制）してから初めて前後方向の不安定化が議論できる。
- **slope 8°+ で1-3歩出るが転倒**: エネルギー過多で発散モード。受動歩行的な fall-and-catch
  には届かない。
- **slope 5°以下で1歩で停止**: エネルギー不足で減衰モード。
- **間の安定リミットサイクル領域は極めて狭い**: パラメータ・初期条件のグリッド探索を
  系統的にやらないと見つからない。これは Phase 3 の MAP-Elites との関連で再訪する余地あり。
- **足球サイズ 0.03 / 0.10 / 0.15 で挙動はほぼ不変**: Genesis の点接触ベース実装では
  rocker foot 効果（連続的接触点移動）が再現されない。真の rocker foot には curved cylinder
  などの別実装が必要（Phase 2 で再検討候補）。
- **接触ソルバーチューニング (constraint_timeconst, integrator, tolerance) は無効果**:
  数値設定の問題ではなく、モデル本体の問題。Genesis のデフォルト設定でも十分。

### 注意点

- 物理ステップは 1ms を維持。受動歩行は数値的に厳しい。

---

## Phase 2: Kneed Walker

**目的:** ぽんぽこ殿が当時扱った研究対象本体に到達する。Phase 3でMAP-Elitesに渡す評価関数の入出力をここで固める。

膝関節を持つ受動歩行機。前進中は膝がロックされ、後退時にフリーになる。よりリアルな人間の歩行に近い。

### 完了条件（再設定 2026-05-03）

Phase 1 と同じ理由（Genesis のフルフィジカルシミュレーションでは
analytical limit cycle の basin of attraction が狭く、kneed walker では
compass よりさらに狭い）で、当初の「歩く」を満たすには大規模な探索が必要と判明。
Phase 3 (MAP-Elites) こそがその探索手法そのものなので、Phase 2 では
**「Phase 3 を走らせるためのインフラ」を整備すること** に集中する。

- [x] 膝関節 (PD ligament + URDF passive damping) を持つモデルが、
      stance leg knee を伸展状態でロックする機構が機能する
- [x] パラメータ空間 (KneedParams dataclass) が明示されている
- [x] パラメータを CLI で変えられる
- [x] 歩行軌跡をログに出力できる (trajectory.jsonl + meta.json)
- [ ] Compass Gait と同じ実行インターフェースで動く `simulate(params, ic)` 純関数
- [ ] パラメータをランダムにサンプルして 100 回シミュレートし、生存率と
      平均歩行距離をログに出せる
- [ ] 1 回のシミュレーションが headless で十分高速に終わる
      (目標: 数秒以下。Phase 3 でバッチ処理可能なレベル)

### このフェーズで決まったこと

- **膝のロック機構**: McGeer 流の純機械的ストッパー (URDF joint limit のみ) では
  立位を保てず両膝が屈曲方向に折れた。stance leg の knee に PD ligament
  (kp/kv で knee=0 へ復元) を毎ステップ印加 + URDF damping を両 knee に入れる
  ハイブリッド方式を採用。動物の靱帯 (spring) + 関節液 (damper) の物理モデル
  に対応する。
- **stance/swing 判定**: 足球 z 座標の比較 + 5 mm のヒステリシス。毎ステップ判定。
- **PD ゲイン**: kp=500, kv=20 がスタート値。`--knee-kp/--knee-kd` で振れる。
- **URDF joint effort**: `effort="0"` だと `control_dofs_force` が無効化されるため
  `effort="1000"` に設定。
- **WalkerParams の最終形**: `KneedParams` dataclass（脚関連 + `knee_damping`
  + `knee_limit_upper` + `slope_deg`）。Phase 3 で Genotype として使用予定。
- **WalkResult**: `trajectory.jsonl` で持つ全 DOF 時系列 + `meta.json` の
  result ブロックで持つ最終状態。Behavior Descriptor の軸候補は trajectory
  から後処理で計算。

### 観察知見 (Phase 2 で確認された事実)

- **kneed walker は compass walker よりさらに歩かせにくい**: knee 自由度が
  増えるぶん, basin of attraction はさらに狭くなる。文献でも McGeer は
  compass を解析したあと kneed を別論文で扱う構成にしている。
- **stance leg knee の伸展ロックには PD ligament が必要**: 純粋な URDF joint
  limit (lower=0) だけでは shin の重力モーメントで膝が屈曲方向に折れる。
- **swing leg knee の屈曲は重力でも起きない場合がある**: swing hip が後傾
  しているとき、shin の重力モーメントは knee を伸展側に押す。動物の歩行で
  swing knee が屈曲するのは、hip が前進する間に shin が後ろに残る慣性結合
  によるもの。Genesis のシミュレーションではこの結合が弱く、結果として
  両脚伸展状態に固まる場合が多い。
- **stance flip (heel-strike) を起こすには「swing leg が前方に振り出される」
  動作が要**: 観察では swing leg が後方に残ったまま体だけ前傾し、結果として
  stance 側の足が常に下にあって flip が起きない試行が多かった。
- **Phase 3 (MAP-Elites) で「basin の探索」自体を行うのが筋**: ぽんぽこ殿の
  研究目的「設計空間の地図」と直結する。Phase 2 ではそのインフラを整備する。

### 注意点

- 膝のロック機構の実装方法は Genesis API で複数選択肢がある。Phase 2 で
  PD ligament 方式を採用したが、Phase 3 で挙動を見ながら再検討の余地あり。
- Phase 3 で MAP-Elites に渡すため、評価関数は **副作用なし・状態を持たない**
  純関数として書く (2.4 で実装)。

---

## Phase 3 以降

`GOALS_NEXT.md` で扱う。現時点ではスコープ外。