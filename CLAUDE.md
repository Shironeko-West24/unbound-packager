# unbound-packager：Claude Code の作業規則

DNS リゾルバ（Unbound）の rpm と deb を、上流の最新版から GitHub Actions で作って公開するリポジトリです。Claude Code でこのリポジトリを変更するときは、この規則に従います。

## 進め方

1. 変更の前に、`README.md`、`docs/` 以下、関係するソースを読む。
2. 変更は作業用のブランチで行い、プルリクエストで出す。マージはリポジトリの管理者が行う。
3. 変更したら、自動テストを実行する：`python3 -I tests/run_all.py`
4. プルリクエストでは、`ci` ワークフローが実ビルド、パッケージのテスト、`unbound-update` の実地テストを行う（文書だけの変更では、自動テストだけを行う）。失敗したら原因を調べて直し、何をなぜ変えたかを説明してからプッシュする。

`ci` の結果は、GitHub の MCP ツールで調べます。どれも読み取りだけです。

| 調べること | ツール |
| --- | --- |
| ブランチの実行の一覧 | `actions_list`（`method: list_workflow_runs`、`workflow_runs_filter.branch` にブランチ名） |
| 実行のジョブの一覧 | `actions_list`（`method: list_workflow_jobs`、`resource_id` に実行 ID） |
| ジョブのログ | `get_job_logs`（`job_id` にジョブ ID、`return_content: true`。足りなければ `tail_lines` を増やす） |

GitHub の MCP ツールがない環境では、REST API を `gh api` で呼びます。

```
gh api "repos/{owner}/{repo}/actions/runs?branch=ブランチ名&per_page=5"
gh api "repos/{owner}/{repo}/actions/runs/実行ID/jobs"
gh api "repos/{owner}/{repo}/actions/jobs/ジョブID/logs"
```

## 禁止事項

- `main` へ直接プッシュしない。変更は必ずプルリクエストで出す。
- `release` ワークフローを起動しない。リリース、タグ、シークレットを作成・変更・削除しない。
- ワークフローに外部のアクション（`uses:`）を足さない。例外は、コミットの SHA で固定した `actions/upload-artifact` と `actions/download-artifact` だけ。`permissions` を広げない。シークレットを渡すステップを増やさない。必要だと考えたら、理由を示して管理者に相談する。
- DNS サーバーやネットワークに関する情報（IP アドレス、ホスト名、ローカルの名前）と、Webhook URL を、リポジトリ、Issue、プルリクエスト、ログに書かない。
- 作業の記録、やり取りの内容、確かめていないことの一覧など、不特定多数に向けたものではない内容を、リポジトリ、Issue、プルリクエストに書かない。
- コミットメッセージとプルリクエストに、セッションへのリンクを書かない。共同作者を書くときは `Co-Authored-By: Claude <noreply@anthropic.com>` とし、モデル名を書かない。
- テストを弱めたり省いたりして、通ったことにしない。確かめていないことを確認済みと書かない。
- ライセンスの検査（`packaging/options.json` の `allowed_packaging_licenses` と `allowed_spec_licenses`）を、管理者の了解なしに緩めない。
- 同じ失敗が 3 回続いたら、止まって状況を報告する。

## 書き方

- 画面の表示、通知、文書は、自然な日本語のです・ます調。
- コードにコメントを書かない（shebang と lint への指示は可）。テスト `test_code_has_no_comments` が検査します。
- `README.md` は、訪れた人向けの紹介として短く保つ。運用の手順は `docs/` に書く。個別の環境や移行の事情など、持ち主だけに関係する内容は、リポジトリに書かない。
- OS やソフトウェアの仕様は、公式文書や公式のソースパッケージで確かめ、`docs/verification.md` の「出典」に URL と情報取得日を書く。

## 参照先

- リポジトリの紹介（訪れた人向け）：`README.md`
- `unbound-update` の使い方：`docs/usage.md`
- リポジトリの運用：`docs/maintenance.md`
- 自動で確かめていることと、出典：`docs/verification.md`
- 出どころとライセンス：`NOTICE.md`
- 脆弱性の報告先とサポートの範囲：`SECURITY.md`
- ビルドオプション、サーバー設定、ライセンスの許可リスト、確認に使う名前：`packaging/options.json`
