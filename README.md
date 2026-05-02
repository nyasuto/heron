# Heron

受動歩行機械の現代スタックによるリバイバル研究プロジェクト。

20年前にOpen Dynamics Engineで遺伝的アルゴリズムを使ってやっていた受動歩行機械の形状最適化を、Genesis + pyribs（MAP-Elites）で再構築する。

## 背景

大学院時代の研究テーマの自己リバイバル。

- **当時:** ODE + GA、目的関数 = 歩行距離、最強解1個を探す。
- **今回:** Genesis + Quality Diversity、設計空間の地図を作る。

20年経って、シミュレータも最適化アルゴリズムも変わった。同じ問題に再挑戦して、当時は得られなかった「設計空間の地図」を可視化することがゴール。

## 技術スタック

- **物理シミュレーション:** Genesis 0.4.x（Apple Metal対応、M4 Mac miniでネイティブ動作）
- **Quality Diversity:** pyribs（MAP-Elites）
- **言語/ランタイム:** Python 3.11+ / uv
- **可視化:** Phase 4で別途検討（Go + WebGL予定、別リポジトリで切り出す可能性あり）

## 開発フェーズ

| Phase | 内容 | モード |
|-------|------|--------|
| 0 | 環境構築、Genesisの感触掴み | 対話 |
| 1 | Compass Gait Walker（膝なし2脚倒立振子） | 対話 |
| 2 | Kneed Walker（膝付き受動歩行機） | 対話 |
| 3 | pyribs統合、MAP-Elites本番 | 対話＋一部Ralph Loop |
| 4+ | 可視化、拡張（POET等） | Ralph Loop主体（別リポジトリ） |

詳細は `GOALS.md` を参照。

## 開発スタイル

Claude Codeとの対話協調開発。研究的観察フェーズはぽんぽこ殿が見て判断、機械的な実装はClaudeに任せる。Phase 0〜2は対話モード。

## セットアップ

```bash
uv sync
uv run python scripts/fall_test.py
```

（Phase 0完了時点での想定。実際のスクリプト名は実装時に確定。）

## ライセンス

個人プロジェクト。今のところ未定。