# krita-agent-bridge

Krita をエージェントから安全に自動操作するための外部ブリッジ実験です。

このプロジェクトでは、Krita AI Diffusion プラグイン本体へ直接自動化機能を組み込むのではなく、独立した外部ツールとして連携する構成を検証します。

> **Disclaimer:** This is an independent, community-driven project. It is not affiliated with, endorsed by, or connected to the Krita project, the Krita AI Diffusion plugin, or ComfyUI.

## 目的

コーディングエージェントとローカルのクリエイティブツールの間に、小さく、確認しやすいブリッジを用意します。

```text
Agent / CLI
  -> krita-agent-bridge
      -> Krita ドキュメントアダプタ
      -> 任意の Krita AI Diffusion アダプタ
      -> 任意の ComfyUI アダプタ
```

最初に想定しているワークフローは次の形です。

```text
Pi Coding Agent -> bridge -> Krita -> Krita AI Diffusion -> ComfyUI
```

AI Diffusion が利用できない環境でも、Krita 単体の自動操作に使える設計を目指します。

## 非目標

- MVP のために upstream の Krita AI Diffusion 変更を必須にしない。
- Krita AI Diffusion の内部実装をコピーしたり vendoring したりしない。
- デフォルトでは `127.0.0.1` を超えてネットワークサービスを公開しない。
- 明示的な opt-in なしに、キャンバスやファイルへの破壊的操作を自動化しない。

## アダプタ境界

| アダプタ | 安定性 | 目的 |
| --- | --- | --- |
| Krita ドキュメントアダプタ | 優先 | アクティブドキュメント、キャンバス書き出し、レイヤー取り込み、選択範囲、基本的なドキュメント操作 |
| Krita AI Diffusion アダプタ | 任意 / 内部 API 扱い | プラグインの有無検出、安全な範囲でのモデル状態取得、薄い shim がある場合の生成実行 |
| ComfyUI アダプタ | バックエンド確認で優先 | `/object_info`, `/prompt`, `/history`, queue, output の確認 |

## 安全デフォルト

- ローカル HTTP サービスは `127.0.0.1` のみに bind する。
- ブリッジ経由のコマンドと生成物をログに残す。
- AI Diffusion の内部 API は不安定な capability として扱う。
- 生成画像、レポート、ローカル設定は git に入れない。

## コントリビューション

Issue や Pull Request は歓迎します。バグ報告、設計相談、ユースケース提案、小さなドキュメント修正など、気軽に送ってください。

特に歓迎する内容:

- Krita 自動操作のユースケース
- 安全なローカル連携の設計案
- CLI 診断やテストの改善
- ドキュメントの不足や分かりにくい箇所の指摘

## 実装状況

| モジュール | 状態 | Issue |
| --- | --- | --- |
| CLI / diagnostics (`doctor`, `status`) | ✅ 実装済み | #2, #7 |
| ComfyUI adapter | ✅ 実装済み | #6 |
| Krita document adapter | ✅ 実装済み | #4 |
| AI Diffusion capability adapter | ✅ 実装済み | #5, #18 |
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
