# main ブランチ保護 一括復元

> このリポジトリは現在「自由開発体制 (main 直 push 可)」に切替え済み。
> レビュー時間が取れるようになったら一発で元に戻すための仕掛け。

## 戻すとき (一発)

```pwsh
pwsh tools/restore-branch-protection.ps1
```

復元される内容:

- `enforce_admins: true` (オーナーも main 直 push 不可)
- `required_status_checks: ["Check minimum approvals"]` (review-gate 通過必須)
- `required_linear_history: true`
- `allow_force_pushes: false`
- `required_conversation_resolution: true`

## なぜ直 push に切替えたか

- レビュー時間が取れない期間限定の運用。
- `.github/workflows/{review-gate,pr-gate,issue-gate,approve-contributor}.yml`
  は残置しているので、PR を出したときだけ自動的に厳格モードに戻る。
  (= PR 経由でも作業できる二刀流)

## ファイル

| ファイル | 役割 |
|---|---|
| `branch-protection-backup.json` | 切替直前の生 protection JSON (記録用) |
| `branch-protection-restore.json` | `gh api PUT` に渡す復元 payload |
| `restore-branch-protection.ps1` | 復元ワンライナー |
