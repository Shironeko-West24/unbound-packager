# 検証と出典

## 自動で確かめていること

### 自動テスト（`python3 -I tests/run_all.py`）

| 対象 | 内容 |
| --- | --- |
| 版番号とリリースの判定 | 版番号の読み取り、公開済みの判定、作り直しのビルド番号、失敗の回数による打ち切り |
| パッチの判定 | 実際の `patch` と `git` を使い、採用、3-way マージでの当て直し、除外（取り込み済み、統一方針、Changelog、NLnet Labs の告知、上流のコミット）を確かめる。`-p` の値の扱いを含む |
| spec と debian の書き換え | 版と Release、パッチの宣言、`configure` の統一（ライブラリだけのビルドの扱いを含む）、`%changelog`、サーバー設定の統一、Maintainer の置き換え |
| 署名とライセンス | テスト用の鍵での上流の署名の検証、鍵の指紋の照合、同梱した G2 の鍵をネットワークなしで使うこと、リリースへの署名と検証（鍵の不一致と署名のない公開の拒否）、公開鍵が各所で同じであること、パッケージングのライセンスの検査 |
| `unbound-update` | 取得、署名の確認（署名のない、改ざんされた、別の鍵で署名されたリリースの拒否）、照合、依存関係の事前確認、入れ替え、名前解決の確認と確認し直し、切り戻し、移行、除外と解除、二重実行の防止（手元の HTTP サーバーと、模擬の dnf、apt、systemctl を使う） |
| 公開と通知 | リリースの組み立て、署名、公開の呼び出し、失敗の記録、通知の文面と秘密の伏せ字（トークンを含む。模擬の GitHub API を使う） |
| リポジトリ | ワークフローの外部のアクションが、SHA で固定した公式の成果物のアクションだけであること、ジョブごとの権限、シークレットの渡し先、定期実行がないこと、コードにコメントがないこと |

### `ci` ワークフロー（プルリクエストごと）

1. 自動テスト
2. 上流の最新版の取得と、リリース署名鍵 G2 による署名の検証
3. Rocky Linux 10 と Ubuntu 26.04 のコンテナでのビルド（各 OS のソースパッケージを使い、パッチを判定する）
4. 両 OS のコンテナでのパッケージのテスト：全サブパッケージの導入、統一したビルドオプションとサーバー設定、モジュールの並び、PIE と RELRO NOW、リンク先のライブラリ、ライセンス文書、各コマンド、Python バインディング、`unbound-checkconf`、起動と安定、名前解決と DNSSEC の検証
5. 両 OS のコンテナでの `unbound-update` の実地テスト（実行ごとに作る一時的な鍵でリリースに署名する）：署名のないリリースの拒否、初回の導入、OS 標準の unbound の除外、設定の保存と取り込み、入れ替え、確認に失敗したときの切り戻し、改ざんされたパッケージの拒否、コマンド自体の更新、解除

文書（`README.md`、`SECURITY.md`、`NOTICE.md`、`CLAUDE.md`、`LICENSE`、`docs/` 以下）だけを変えたプルリクエストでは、1 だけを行います。

ファイアウォールの操作は、コンテナでは動かせないため、実地テストに含まれません。

## 出典

情報取得日：2026年11月15日（上流の配布ページと署名鍵は 2026年11月14日）

| 内容 | 確認した箇所 | URL | 種別 |
| --- | --- | --- | --- |
| 上流の最新版と、署名・チェックサムのファイル名 | 配布ページの一覧 | https://nlnetlabs.nl/downloads/unbound/ | 一次情報 |
| リリース署名鍵 G2 の指紋と公開場所 | 告知 | https://www.nlnetlabs.nl/news/2025/Dec/09/keyroll-2025/ | 一次情報 |
| CVE の影響を受ける版（パッチの判定に使う「Affects:」） | Security Advisories の各項目 | https://nlnetlabs.nl/projects/unbound/security-advisories/ | 一次情報 |
| `--disable-sha1` で SHA-1 系の署名のゾーンが未署名扱いになること | 上流の開発者の投稿 | https://lists.nlnetlabs.nl/pipermail/unbound-users/2021-May/007383.html | 一次情報（開発者の発言） |
| Unbound と同梱ファイルのライセンス、`debian/` 以下が GPL-3 以降であること | Debian 安定版の `debian/copyright` | https://metadata.ftp-master.debian.org/changelogs/main/u/unbound/stable_copyright | 一次情報 |
| Fedora で、ライセンスの指定がない貢献の既定が MIT であること | Fedora Project Contributor Agreement | https://docs.fedoraproject.org/az/legal/fpca/ | 一次情報 |
| Rocky Linux 10 の unbound のソースパッケージの作り（`%autopatch -p2`、`%{gpgverify}` の鍵、`root.key` がトラストアンカーであること） | SRPM の spec の `%prep` と `%install` | https://dl.rockylinux.org/pub/rocky/10/AppStream/source/tree/Packages/u/unbound-1.24.2-7.el10_2.6.src.rpm | 一次情報 |
| Ubuntu の改変版を配るときの商標の扱い | Canonical の知的財産権の方針 | https://canonical.com/legal/intellectual-property-policy?format=md | 一次情報 |
| RESF（Rocky Linux）の商標の指針 | 商標の利用指針 | https://wiki.rockylinux.org/team/design/branding/ | 一次情報 |
| 公開リポジトリで標準のランナーが無料であること | 「GitHub Actions の課金」 | https://docs.github.com/en/billing/concepts/product-billing/github-actions | 一次情報 |
| 公開リポジトリで、60 日間動きがないと定期実行が無効になること | 「ワークフローの無効化と有効化」 | https://docs.github.com/en/enterprise-server@3.16/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows | 一次情報 |
| 外部の貢献者のワークフローに承認が要ること | 「公開フォークからのワークフロー実行の承認」 | https://docs.github.com/en/enterprise-server@3.12/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/approving-workflow-runs-from-public-forks | 一次情報 |
| リポジトリの「Actions permissions」の選択肢の名前、許可するアクションの書き方（`OWNER/REPOSITORY@TAG-OR-SHA`、`,` 区切り）、自分のアカウントのアクションだけを許可すると GitHub が作ったアクションも使えなくなること、`*` で版を問わず許可できること | 「リポジトリの GitHub Actions の設定の管理」の原稿と、そこから読み込まれる共通の文面（`allow-specific-actions-intro`、`actions-use-policy-settings`、`policy-label-for-select-actions-workflows`）（GitHub の文書の公開リポジトリにある原稿） | https://github.com/github/docs/blob/main/content/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository.md 、https://github.com/github/docs/blob/main/data/reusables/actions/allow-specific-actions-intro.md | 一次情報（2026年11月19日取得） |
| ジョブの `env` で `runner` コンテキストを使えないこと | 「Contexts reference」の「Context availability」の表 | https://raw.githubusercontent.com/github/docs/main/content/actions/reference/workflows-and-actions/contexts.md | 一次情報 |
| ランナーの既定の環境変数 `RUNNER_TEMP` | 「Variables reference」の既定の環境変数の表 | https://raw.githubusercontent.com/github/docs/main/content/actions/reference/workflows-and-actions/variables.md | 一次情報 |
| `ssh-keygen -Y sign` と `-Y verify`、`allowed_signers` の書式（`namespaces=`） | ssh-keygen(1) の `-Y sign`、`-Y verify`、「ALLOWED SIGNERS」 | https://man.openbsd.org/ssh-keygen.1 | 一次情報 |
| 対応するソースを必要な期間入手できるようにする義務 | GPL-3 第 6 条 d | https://www.gnu.org/licenses/gpl-3.0.txt | 一次情報 |
| 非公開の脆弱性報告を、公開リポジトリの管理者が有効にできること | 「Configuring private vulnerability reporting for a repository」 | https://raw.githubusercontent.com/github/docs/main/content/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository.md | 一次情報 |
| 報告する人が「Security」から「Report a vulnerability」で非公開の報告を送ること | 報告の手順（再利用される文書の断片） | https://raw.githubusercontent.com/github/docs/main/data/reusables/security-advisory/reporting-a-vulnerability-non-admin.md | 一次情報 |
| Unbound の脆弱性の報告先 | NLnet Labs の「Security Report」 | https://nlnetlabs.nl/security-report/ | 一次情報 |
| リリース署名鍵 G2 の配布場所 | 鍵のファイル | https://nlnetlabs.nl/downloads/keys/releases-g2.asc | 一次情報 |
| 成果物は REST API では作れず、アクションで保存すること | 「REST API endpoints for GitHub Actions artifacts」 | https://raw.githubusercontent.com/github/docs/main/content/rest/actions/artifacts.md | 一次情報 |
| `actions/upload-artifact` v7.0.2 のコミットと入力（`name`、`path`、`if-no-files-found`、`retention-days`） | タグ v7.0.2 の `action.yml` | https://raw.githubusercontent.com/actions/upload-artifact/cf430e030ddbb5b0abf93d22962f4752f3646cd9/action.yml | 一次情報 |
| `actions/download-artifact` v8.0.2 のコミットと入力（`name`、`path`） | タグ v8.0.2 の `action.yml` | https://raw.githubusercontent.com/actions/download-artifact/9000827ccba6bdab643e8b6fd33ac0654aef8333/action.yml | 一次情報 |
| パスの条件でワークフローを省くと、必須のチェックが「Pending」のまま残ること（`paths-ignore` を使わない理由） | 「Troubleshooting required status checks」の表 | https://raw.githubusercontent.com/github/docs/main/content/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks.md | 一次情報 |
| ランナーのラベル `ubuntu-26.04` | runner-images の「Available Images」の表 | https://raw.githubusercontent.com/actions/runner-images/main/README.md | 一次情報 |
| `ubuntu-26.04` のランナーに Podman（5.7.0）と systemd（259.9）が入っていること | Ubuntu 26.04 のイメージの説明（イメージの版 20261111.196.1） | https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu/Ubuntu2604-Readme.md | 一次情報 |
