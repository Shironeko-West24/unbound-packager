# unbound-packager

[Unbound](https://nlnetlabs.nl/projects/unbound/about/) を上流の最新ソースからビルドした、Rocky Linux 10 向けの rpm と Ubuntu 26.04 向けの deb を公開するリポジトリです。

個人による非公式のビルドです。NLnet Labs、Rocky Enterprise Software Foundation、Red Hat、Canonical、Debian のいずれとも関係がありません。無保証です。

## 公開するもの

各[リリース](../../releases)に、次のものを置きます。

| 対象 | 内容 |
| --- | --- |
| Rocky Linux 10 | OS 標準の unbound と同じ構成の rpm 一式（ライブラリ、Python バインディング、開発用ヘッダ、unbound-anchor などを含む）と SRPM |
| Ubuntu 26.04 | OS 標準の unbound と同じ構成の deb 一式と、ソースパッケージ |
| 共通 | `SHA256SUMS`、`manifest.json`（パッチの判定結果を含む）、更新用のコマンド `unbound-update` |

## 特徴

- 上流の新しい版が出ると、GitHub Actions が自動でビルドし、コンテナでテストしてから公開します。
- パッケージングは各 OS の公式ソースパッケージを使い、上流の版だけを差し替えます。新しい版に取り込み済みのパッチは外します。
- ビルドオプションを両 OS で統一しています（[`packaging/options.json`](packaging/options.json)）。
  - 有効: Python モジュールとバインディング、subnet、dnstap、systemd、DoH、TCP Fast Open、ECDSA、SHA-2
  - 無効: cachedb、ipsecmod、GOST、SHA-1 署名の検証（RFC 9905 に沿う）、既定の chroot（設定で指定したときだけ chroot します）
- 上流のソースは、NLnet Labs のリリース署名鍵 G2 で署名を検証します。

## 使い方

DNS サーバーに `unbound-update` を置き、取得元を設定します。

```
curl -fsSLO https://github.com/Shironeko-West24/unbound-packager/releases/latest/download/unbound-update
curl -fsSLO https://github.com/Shironeko-West24/unbound-packager/releases/latest/download/SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS
sudo install -m 755 unbound-update /usr/local/sbin/unbound-update
sudo unbound-update init --repository Shironeko-West24/unbound-packager
```

最初の導入と、以後の更新は次のとおりです。

```
sudo unbound-update migrate
sudo unbound-update update
```

`unbound-update` は、パッケージを取得して SHA-256 を照合し、導入、`unbound-checkconf`、起動と名前解決の確認を行います。どれかに失敗したら、元の版と設定へ切り戻します。OS 標準の unbound は、自動更新の対象から外します。

設定項目、副作用と戻し方、うまくいかないときの対処は、[docs/usage.md](docs/usage.md) にあります。

## 注意

- 照合で確かめられるのは「このリポジトリのリリースにあるものと同じ」ことまでです。
- SHA-1 署名の検証を無効にしているため、SHA-1 系の署名だけのゾーンは、未署名として扱われます。
- 自分で運用したい場合は、フォークして使ってください（[docs/maintenance.md](docs/maintenance.md)）。

## ライセンス

このリポジトリのコードと文書は MIT License です（[LICENSE](LICENSE)）。公開するパッケージの出どころとライセンスは、[NOTICE.md](NOTICE.md) にあります。
