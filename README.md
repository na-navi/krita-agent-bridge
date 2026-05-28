# krita-agent-bridge

Krita のローカルスクリプト操作と CLI 診断を扱うための外部ブリッジ実験です。

このプロジェクトでは、Krita 本体や既存プラグインへ自動化機能を直接組み込むのではなく、独立した外部ツールから確認しやすい操作だけを小さく接続する構成を検証します。

> **Disclaimer:** This is an independent, community-driven project. It is not affiliated with, endorsed by, or officially associated with the Krita project, the Krita AI Diffusion plugin, or ComfyUI.

## 目的

コーディングエージェントや CLI と、ローカルの Krita ワークフローの間に、小さく、確認しやすいブリッジを用意します。

主な対象は、ドキュメント状態の確認、キャンバスやレイヤーの入出力、ローカル環境の診断、非破壊的な操作フローです。

```text
Agent / CLI
  -> krita-agent-bridge
      -> Krita ドキュメントアダプタ
      -> ローカル診断 / safety checks
      -> 任意のローカル連携アダプタ
```

任意連携として、利用者のローカル環境に存在する追加プラグインやバックエンドを検出・利用できる余地を残しています。ただし、それらは必須依存ではなく、Krita 単体の自動操作にも使える設計を目指します。

## 非目標

- MVP のために upstream の Krita や既存プラグインの変更を必須にしない。
- 既存プラグインの内部実装をコピーしたり vendoring したりしない。
- デフォルトでは `127.0.0.1` を超えてネットワークサービスを公開しない。
- 明示的な opt-in なしに、キャンバスやファイルへの破壊的操作を自動化しない。

## アダプタ境界

| アダプタ | 安定性 | 目的 |
| --- | --- | --- |
| Krita ドキュメントアダプタ | 優先 | アクティブドキュメント、キャンバス書き出し、レイヤー取り込み、選択範囲、基本的なドキュメント操作 |
| 任意プラグイン capability アダプタ | 任意 / 内部 API 扱い | 利用者が導入済みのプラグインの有無検出と限定的な capability 確認 |
| 任意バックエンドアダプタ | 任意 | ローカルバックエンドの疎通確認、queue、output の確認 |

## 安全デフォルト

- ローカル HTTP サービスは `127.0.0.1` のみに bind する。
- ブリッジ経由のコマンドと生成物をログに残す。
- 任意プラグインの内部 API は不安定な capability として扱う。
- 生成画像、レポート、ローカル設定は git に入れない。

## コントリビューション

Issue や Pull Request は歓迎します。バグ報告、設計相談、ユースケース提案、小さなドキュメント修正など、気軽に送ってください。

特に歓迎する内容:

- Krita 自動操作のユースケース
- 安全なローカル連携の設計案
- CLI 診断やテストの改善
- ドキュメントの不足や分かりにくい箇所の指摘

## 実装状況

| モジュール | 状態 | GitHub Issue |
| --- | --- | --- |
| CLI / diagnostics (`doctor`, `status`) | ✅ 実装済み | #2, #7 |
| Krita document adapter | ✅ 実装済み | #4 |
| Optional backend adapter | ✅ 実装済み | #6 |
| Optional plugin capability adapter | ✅ 実装済み | #5, #18 |
| Prompt simplification / prepare layer | ✅ 実装済み | #17, #19 |
| Job status monitoring | ✅ 実装済み | #21 |
| Unified snapshot | ✅ 実装済み | #20 |
| E2E smoke workflow | 📋 計画中 | #8 |

詳しくは `PLANS.md` を参照してください。

- `docs/safety.md` — 安全方針
- `docs/recipes.md` — エージェント向けレシピ
- `docs/guides/comparison-pi-api.md` — 旧 pi_api との機能比較

## ライセンス

MIT。詳しくは `LICENSE` を参照してください。
