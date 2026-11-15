# 出どころとライセンス

このリポジトリが公開するパッケージは、個人による非公式のビルドです。NLnet Labs、Rocky Enterprise Software Foundation、Red Hat、Canonical、Debian のいずれとも関係がなく、いずれの承認や推奨も受けていません。名称は、対応する OS や由来を示すためだけに使っています。

## このリポジトリのコード

`lib/`、`packaging/`、`server/`、`tests/`、`tools/`、`.github/` 以下のコードと文書は、MIT License です（`LICENSE`）。

## 公開するパッケージに含まれるもの

| 対象 | 由来 | ライセンス |
| --- | --- | --- |
| Unbound 本体 | NLnet Labs の公式ソース（https://nlnetlabs.nl/downloads/unbound/ ）。署名を検証して取得 | BSD 3-Clause。一部のファイルは ISC など。全文はパッケージ内の LICENSE、または `copyright` にあります |
| rpm のパッケージング（spec、ユニットファイル、既定の設定など） | Rocky Linux 10 の unbound のソースパッケージ（Fedora と RHEL に由来） | Fedora の既定では MIT。個別の表記がある場合はそれに従います |
| deb のパッケージング（`debian/` 以下） | Ubuntu 26.04 の unbound のソースパッケージ（Debian に由来） | GPL-3 以降。`debian/patches/` は ISC。全文は `/usr/share/doc/unbound/copyright` と `/usr/share/common-licenses/GPL-3` にあります |

## 加えた変更

ビルドのたびに、次の変更を機械的に加えています。変更の内容と日付は、rpm の `%changelog` と deb の `debian/changelog` に記録されます。

- 上流の版を、OS のソースパッケージの版から最新版へ差し替える。
- 新しい版に含まれている修正のパッチを外し、必要なパッチを当て直す（判定の結果は、各リリースの説明と `manifest.json` にあります）。
- ビルドオプションと、既定の設定の一部（`harden-referral-path`）を両 OS で統一する（`packaging/options.json`）。
- 版番号に `local` を付ける。Maintainer、Vendor、Packager をこのリポジトリのものに置き換える。

## ソースの入手

各リリースに、バイナリパッケージと同じ場所で、対応するソースパッケージを置いています。

- rpm: `unbound-*.src.rpm`
- deb: `unbound_*.dsc`、`unbound_*.debian.tar.*`、`unbound_*.orig.tar.gz`

リリースは削除せず、残し続けます。バイナリを受け取った人が、対応するソースを入手できるようにするためです。

ビルドの手順は、このリポジトリの `packaging/` と `.github/workflows/` にあります。

## 同梱する鍵

`packaging/keys/nlnetlabs-releases-g2.asc` は、NLnet Labs が公開しているリリース署名鍵 G2（https://nlnetlabs.nl/downloads/keys/releases-g2.asc ）の写しです。上流のソースの署名を確かめるためだけに使います。
