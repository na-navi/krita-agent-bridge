# pi_api (旧) vs krita-agent-bridge (新) 機能比較

作成日: 2026-05-28

旧 pi_api / fork 拡張は、upstream PR を前提に継続するのではなく、参考実装として archive 済みです。以後の実験は独立した外部ブリッジである `krita-agent-bridge` 側に移します。

## 概要の違い

| | **pi_api (旧)** | **krita-agent-bridge (新)** |
|---|---|---|
| **形態** | archived fork 拡張に注入した `http.server` | 独立した Python パッケージ + CLI (`krita-agent`) |
| **依存** | Krita プロセス内で動作。Krita 必須 | 外部プロセス。Krita なしでも ComfyUI 単体で動く |
| **起動条件** | `KRITA_AI_PI_API=1` 環境変数付き Krita 起動 | `pip install -e .` → `krita-agent` コマンド |
| **ポート** | 8900（固定） | ComfyUI 8188 を直接叩く。Krita 8900 も診断対象 |
| **安定性** | 低（頻繁変更前提の開発テスト用） | 改善見込み（外部アーキテクチャでプラグイン内部依存を分離） |

---

## 新 (bridge) が優れている点

### Krita 非依存で動く

ComfyUI アダプタだけでも完結。Krita が落ちても・なくても画像生成ワークフローが回る。

### 安全設計が明文化

`safety.md` で非破壊デフォルト・`--allow-destructive` ガード・ローカル only バインドが規定。
旧 pi_api には安全ポリシーなし。

### 診断コマンド (doctor)

`krita-agent doctor --json` 一発で、Krita bridge / AI Diffusion / ComfyUI / ポート開放を全診断。
終了コード 0/1/2 で自動判断可能。旧は個別に `curl` を叩くしかなかった。

### ComfyUI 直叩きアダプタ

`/object_info` 取得・ノードスキーマ検証・`/prompt` 投入・`/history` 確認・出力ファイル解決を
すべて型安全な `ComfyUIResult` でラップ。旧は ComfyUI を直接触る手段がなかった。

### ノードバリデーション

`validate_prompt()` でワークフロー内のノード型を `/object_info` と照合。
未知ノードは投入前に弾ける。旧にはない機能。

### エラー分類

`CONNECTION / VALIDATION / EXECUTION` の 3 段階でエラーを分類。
エージェントの自動リカバリが書きやすい。

### CLI インターフェース

`krita-agent status` / `krita-agent doctor` のサブコマンド構成。JSON 出力対応。
旧は `curl` を手書きするしかなかった。

### テスト・型チェック体制

`pyproject.toml` に pyright / ruff / pytest 設定。テストファイルあり。旧はテストなし。

### ドキュメント整備

ADR、safety、recipes、GitHub Issues による管理。旧は SKILL.md のみ。

### stdlib-only

`urllib` のみで依存ゼロ。Krita 内の pi_api も stdlib だが、bridge は Krita プロセス外で
動くのでバージョン競合の心配なし。

### レイヤー取り込み設計

docs に「生成画像は新規レイヤーに追加」と明記。破壊操作には `--allow-destructive` が必要。

### 出力ファイルの絶対パス解決

`resolve_outputs()` が ComfyUI の `output/` から絶対パスを返す。
旧は結果 PNG を `curl -o` で取るだけ。

---

## 新 (bridge) が劣っている / 未実装の点

### 画像生成の E2E 実行が未完成

Phase 6（E2E smoke）は未着手。
bridge だけで `prepare → trigger → result` を完結させるパスはあるが、
実際の Krita + AI Diffusion + ComfyUI で一気通貫したテストはまだです。

### Krita プラグイン検出が受動的

bridge の `doctor` は旧 pi_api の `/api/status` を叩いて AI Diffusion の有無を判定する。
つまり **旧 pi_api が起動している前提** に依存している。

### Python パッケージのインストールが必要

旧は curl だけで叩けた。bridge は `pip install -e .` が必要で、
環境セットアップの壁が 1 つ増える。

---

## 機能マトリクス

| 機能 | 旧 pi_api | 新 bridge | 備考 |
|------|:---------:|:---------:|------|
| 環境診断 | △ (手動 curl) | ✅ `doctor` | 自動化しやすい |
| ステータス確認 | ✅ `/api/status` | ✅ `status` コマンド | 同等（新は旧 API に依存） |
| キャンバス取得 | ✅ `/api/canvas` | ✅ ドキュメントアダプタ | #4 で実装 |
| スタイル一覧 | ✅ `/api/styles` | ✅ `styles()` | #5 で実装 |
| プロンプト設定 | ✅ `prepare` 簡易 | ✅ `PrepareInput` | #17/#19 で実装 |
| 生成トリガー | ✅ `trigger` | ✅ ComfyUI `/prompt` | 新は自由度が高い |
| ジョブ監視 | ✅ `/api/jobs` | ✅ `JobMonitor` | #21 で実装 |
| 結果画像取得 | ✅ `/api/result` | ✅ `resolve_outputs` | 新は絶対パス返却が優秀 |
| ノードバリデーション | ❌ | ✅ `validate_prompt` | 新のみ |
| モード切替 | ✅ `POST /api/mode` | ✅ `set_mode()` | #18 で実装 |
| スナップショット | ✅ `GET /api/snapshot` | ✅ `snapshot()` | #20 で実装 |
| 安全ポリシー | ❌ | ✅ `safety.md` + flag | 新のみ |
| エラー分類 | △ (HTTP status のみ) | ✅ 3 段階分類 | 新が優秀 |
| ドキュメント | △ SKILL.md のみ | ✅ ADR + recipes + safety | 構造化ドキュメントあり |
| テスト | ❌ | ✅ pytest | 新のみ |
| Krita なしで動く | ❌ | ✅ ComfyUI 単体で可 | 外部プロセスで独立動作 |

---

## まとめ

新 `krita-agent-bridge` は、旧 pi_api の主要な操作カテゴリをおおむねカバーしました。
doctor・ノードバリデーション・エラー分類・安全ポリシー・ドキュメント体制は
旧になかった強みとして引き続き優位です。

Phase 2–5 が完了し、ドキュメントアダプタ・AI Diffusion アダプタ（モード切替含む）・
prepare 層・ジョブ監視・スナップショットがすべて揃いました。

残るは Phase 6（E2E smoke workflow）の実証のみです。
