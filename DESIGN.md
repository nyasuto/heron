# 初期設計

Phase 0 〜 2 の範囲での設計方針。Phase 3 以降の構造は、Phase 2 完了時点で見直す。

---

## ディレクトリ構成（提案）

```
heron/
├── README.md
├── CLAUDE.md
├── GOALS.md
├── DESIGN.md
├── pyproject.toml
├── uv.lock
├── src/
│   └── heron/
│       ├── __init__.py
│       ├── walker/
│       │   ├── __init__.py
│       │   ├── compass.py     # Phase 1: Compass Gait
│       │   └── kneed.py       # Phase 2: Kneed Walker
│       ├── sim/
│       │   ├── __init__.py
│       │   ├── runner.py      # Genesisラッパー、シミュレーション実行
│       │   └── logger.py      # 軌跡ログ、CSV/JSONL書き出し
│       └── cli.py             # Phase 1以降、CLIエントリポイント
├── scripts/
│   ├── fall_test.py           # Phase 0: 球を落とすデモ
│   ├── walk_compass.py        # Phase 1: Compass Gaitデモ
│   └── walk_kneed.py          # Phase 2: Kneed Walkerデモ
├── tests/                     # 最小限。物理一致のスモークテスト程度
└── data/
    └── runs/                  # 実行ログ、Git管理外
```

`src/heron/` レイアウト（src layout）にして、エディタブルインストールで使う。

---

## 主要な抽象化

### `WalkerParams`（dataclass）

Phase 1 / Phase 2 共通の基底になる想定。

```python
@dataclass(frozen=True)
class WalkerParams:
    # 共通
    slope_deg: float           # 坂の傾斜
    foot_radius: float         # 足の弧の半径

    # Compass Gait用
    leg_length: float          # 脚の長さ
    leg_mass: float            # 脚1本の質量
    hip_mass: float            # 腰の質量

    # 拡張: Kneed Walkerでは別dataclass、または継承
```

**Phase 3 で MAP-Elites の Genotype になる前提。`frozen=True` でハッシュ可能にしておく。**

### `WalkResult`（dataclass）

シミュレーション結果。

```python
@dataclass
class WalkResult:
    distance: float            # 歩行距離（前進方向）
    steps: int                 # 歩数
    fell: bool                 # 転倒したか
    sim_time: float            # シミュレーション秒数
    trajectory: list[State]    # 時系列（状態ベクトル）
```

`trajectory` から後処理で歩幅・歩行周期・エネルギー効率などを計算する。**Phase 3 で Behavior Descriptor の軸を変えやすくするため、生データを保持する。**

### `simulate` 関数

Phase 3 で MAP-Elites の評価関数として呼ばれる前提。

```python
def simulate(
    params: WalkerParams,
    *,
    max_sim_time: float = 30.0,
    headless: bool = True,
    seed: int = 0,
) -> WalkResult:
    ...
```

**重要な性質:**

- 副作用なし（グローバル状態を持たない）
- 同じ入力で同じ出力（決定論的、`seed` で固定可能）
- Genesis のシーンは関数内で毎回作って捨てる（並列実行時の状態漏れ防止）

---

## Genesis の使い方の方針

- **シーンは毎回作り直す。** 状態漏れを避ける。シーンプール最適化は Phase 3 のパフォーマンス問題が出てから検討。
- **物理ステップは 1ms（0.001s）。** 受動歩行は数値的に敏感なので大きめにしない。
- **レンダリングは headless がデフォルト。** デモスクリプトのみ可視化。
- **Apple Metal バックエンド明示。** 起動ログで確認する習慣。

---

## ログ・観察可能性

- 軌跡ログは JSONL で `data/runs/<timestamp>/trajectory.jsonl` に書き出す
- メタ情報（params、result）は同ディレクトリの `meta.json` に
- スクリプト実行のたびに新しいディレクトリを作る（上書き禁止）

これは Phase 3 で MAP-Elites のアーカイブを再現可能にするための布石でもある。

---

## やらないこと（このスコープで）

- pyribs の実利用（Phase 3）
- 並列シミュレーション実行（Phase 3）
- 可視化ダッシュボード（Phase 4、別リポジトリで切り出す可能性）
- 形と制御の同時最適化（Co-design、未定）
- 強化学習との比較（未定）

---

## 後で見直す予定

- `WalkerParams` の最終形（Phase 2完了時に確定）
- `WalkResult.trajectory` のフォーマット（並列化時のシリアライズコストを見て判断）
- ロボット記述方法（URDF / Genesisネイティブ / 自前 dataclass のどれか、Phase 1で決める）