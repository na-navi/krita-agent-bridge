# kra-remote metadata notes

作成日: 2026-05-29

## ライセンス境界

`NMaghfurUsman/kra-remote` は GPL-3.0 の Krita remote-control plugin であるため、
このメモではコード、diff、テスト、コメント、UI構造、具体的なプロトコル文字列を参照しない。

参照した範囲は、公開メタデータに限定する。

- GitHub repository description: "Krita python plugin for remote control"
- README レベルの説明
- Issue title/body metadata
- Commit message metadata

実装判断の根拠は、この repository の既存設計、Krita official Python API、HTTP + JSON の
localhost-only shim 方針に置く。

## メタデータから見えた対象ベクトル

### Plugin lifecycle

README では Krita の Python plugin import、Krita 再起動、docker 有効化、ユーザー操作での
接続開始が説明されている。ここから、Krita plugin は「インストールできること」だけでなく、
再起動後にメニューやUIから確実に起動できること、すでに読み込まれた Python module cache を
考慮することが重要だと分かる。

`krita-agent-bridge` 側では、ZIP構造テスト、deploy後の再起動要求、起動後の `/api/status`
確認を別々のチェックとして扱うべき。

### Local server lifecycle

commit message には、server close、port collision、connection error handling に関する修正が
複数見える。Krita内でローカルサーバーを動かす場合、起動成功だけでは足りず、終了、再起動、
ポート占有、接続切断を明示的に扱う必要がある。

`krita-agent-bridge` 側では以下を重視する。

- localhost-only bind
- already-running response
- shutdown/restart strategy
- request timeout
- request log
- port collision diagnostics

### Transport security

Issue #1 は TLS を調査対象にしている。README にも安全でないネットワークでの利用注意がある。
これは remote-control 系 plugin では、ローカル操作APIであっても transport exposure が設計上の
中心リスクになることを示す。

`krita-agent-bridge` は当面 localhost-only を維持し、LAN公開、TLS、QR/phone client、browser
client は別問題として扱う。外部公開をする場合は、認証、TLS、ユーザー確認、操作範囲制限を
別Issueに切るべき。

### User-configurable command surfaces

Issue #2/#3 は layout serialization/editor、Issue #4 は modes/layers を対象にしている。
これは「操作を増やす」だけでなく、ユーザーが安全に設定を保存・復元・編集できる構造が必要に
なることを示す。

`krita-agent-bridge` では、remote command execution や eval/exec を避け、明示的な
JSON command schema と adapter method のみを公開する。将来、操作セットを拡張する場合も、
command registry は allowlist 型にし、schema validation と structured error を必須にする。

### Offline/local-first behavior

commit message には、外部オンラインサービス依存を避けてローカル生成に寄せた履歴が見える。
Krita plugin はユーザーの制作環境内で動くため、ネットワーク外部依存は障害点とプライバシー
懸念になりやすい。

`krita-agent-bridge` では、診断、ZIPビルド、shim起動、ログ記録、E2E smoke をローカル完結に
寄せる方針が妥当。

## 感想

kra-remote の公開メタデータからは、Krita内ローカルサーバーの難しさは「Krita APIを叩けるか」
よりも、plugin lifecycle、ポート管理、接続状態、UI/操作面の安全境界にあるように見える。

今回の `krita-agent-bridge` shim で起きた、HTTP handler がKrita操作で詰まる問題も同じ系列の
問題で、Krita main thread へのマーシャリング、リクエストタイムアウト、ログ出力は最初から
土台として持つべきだった。

ただし kra-remote はスマートフォン向け remote-control 体験を中心にした設計であり、
`krita-agent-bridge` はエージェント向けの安全な document/generation adapter が中心である。
そのため、UIやジェスチャ、LAN利用、汎用キー入力の方向へ寄せず、HTTP + JSON、localhost-only、
明示schema、非破壊デフォルト、structured response の軸を維持する。

## 参考メタデータ

- Repository: https://github.com/NMaghfurUsman/kra-remote
- License metadata: GPL-3.0
- Issues observed: #1 TLS, #2 Serializable data structure for layout, #3 Layout editor, #4 Modes/layers
- Commit message themes observed: server shutdown, port collision, connection error handling, local generation,
  event logging, Windows compatibility, disconnect notification
