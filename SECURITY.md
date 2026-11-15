# セキュリティ

## このリポジトリについて

このリポジトリが公開する Unbound のパッケージは、個人による非公式のビルドです。NLnet Labs、Rocky Enterprise Software Foundation、Red Hat、Canonical、Debian のいずれとも関係がありません。無保証です。

## 報告先

| 対象 | 報告先 |
| --- | --- |
| Unbound 本体の脆弱性 | NLnet Labs に報告してください。手順は https://nlnetlabs.nl/security-report/ にあります |
| このリポジトリの脆弱性（パッケージング、ビルドと公開の仕組み、`unbound-update`、リリースの署名） | このリポジトリの「Security」タブの「Report a vulnerability」から、非公開で報告してください |

- 公開の Issue やプルリクエストには、脆弱性の詳細を書かないでください。
- このリポジトリの由来でない問題（Rocky Linux や Ubuntu のパッケージングに元からある問題など）は、それぞれの配布元に報告してください。
- 報告への返信や修正の時期は、約束できません。

## サポートの範囲

| 対象 | 範囲 |
| --- | --- |
| リリース | 最新のリリースだけ。古いリリースは、ソースの入手のために残していますが、修正しません |
| OS | Rocky Linux 10 と Ubuntu 26.04 |
| Unbound の脆弱性の修正 | 上流が修正した版を出したあと、このリポジトリのビルドに取り込みます。独自の修正は行いません |

## リリースの確かめ方

各リリースの `SHA256SUMS` には、このリポジトリ専用の鍵で署名しています（`SHA256SUMS.sig`）。公開鍵は `packaging/keys/release-signing.pub` にあり、`unbound-update` にも埋め込んでいます。確かめ方は [docs/usage.md](docs/usage.md) にあります。
