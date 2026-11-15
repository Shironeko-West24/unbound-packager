# unbound-update の使い方

DNS サーバーにパッケージを導入し、更新するための手順です。対応する OS は Rocky Linux 10 と Ubuntu 26.04 です。

以下、リポジトリを `Shironeko-West24/unbound-packager` として書きます。フォークして使う場合は、読み替えてください。

## 導入

```
cd ~
curl -fsSLO https://github.com/Shironeko-West24/unbound-packager/releases/latest/download/unbound-update
curl -fsSLO https://github.com/Shironeko-West24/unbound-packager/releases/latest/download/SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS
sudo install -m 755 unbound-update /usr/local/sbin/unbound-update
rm unbound-update SHA256SUMS
sudo unbound-update init --repository Shironeko-West24/unbound-packager
sudoedit /etc/unbound-packager/unbound-update.conf
```

- `sha256sum` が `unbound-update: OK` と表示することを確かめてから、導入してください。
- `sudo unbound-update` が見つからないと言われた場合は、`sudo /usr/local/sbin/unbound-update` と書いてください。
- 戻すには `sudo rm -rf /usr/local/sbin/unbound-update /etc/unbound-packager /var/lib/unbound-packager` です。

設定ファイルの項目:

| セクション | キー | 意味 | 既定 |
| --- | --- | --- | --- |
| `[source]` | `repository` | 取得元のリポジトリ（所有者/名前） | 必須 |
| `[packages]` | `install` | 導入するパッケージ名（空白区切り） | 実行に必要なもの（開発用と dracut を除く） |
| `[checks]` | `resolve` | 名前解決を確かめる名前（空白区切り） | `example.com` |
| `[checks]` | `dnssec_valid` | 検証済み（AD）になるはずの名前。空なら確認しない | `nlnetlabs.nl` |
| `[checks]` | `dnssec_bogus` | SERVFAIL になるはずの、署名が壊れた名前。空なら確認しない | 空 |
| `[checks]` | `local` | 確認に加えるローカルの名前（空白区切り） | なし |
| `[checks]` | `lan_address` | このサーバーの LAN 側の IP アドレス。書くと、ここにも問い合わせる | 空 |
| `[checks]` | `stable_seconds` | 起動後、止まらないことを見る秒数 | 15 |
| `[migrate]` | `old_config_dir` | 旧サーバーの設定ディレクトリ | 自動判定 |

- 知らないキーや不正な値は、行番号を付けてまとめて表示し、何も変えずに止まります。
- `lan_address` を書くと、ファイアウォールと `access-control` を通る経路も確かめられます。書くことを勧めます。
- `dnssec_bogus` は、わざと署名を壊してある公開の名前を、`dig +dnssec 名前` が SERVFAIL になることを確かめてから書いてください。既定では空です。

## 最初の導入

最初は `update` ではなく `migrate` を実行します。

```
sudo unbound-update migrate
```

行うことは次のとおりです。

1. パッケージを取得して SHA-256 を照合し、依存関係を確かめます（ここまではサーバーを変更しません）。
2. パッケージを導入します。
3. `unbound-checkconf` を実行します。
4. OS 標準の unbound を自動更新の対象から外し、ファイアウォールで DNS を許可します（内容は「副作用と戻し方」）。
5. unbound を起動し、止まらないことと名前解決を確かめます。

別のサーバーの設定を引き継ぐ場合は、元のサーバーで `sudo unbound-update save --output ファイル` を実行して設定を保存し、新しいサーバーで `sudo unbound-update migrate --config ファイル` を実行します。設定ディレクトリが `/etc/unbound` 以外だった場合は、設定の中のそのパスを `/etc/unbound` に置き換えます。

## 更新

```
sudo unbound-update update --version 1.26.2
```

サーバーの中で行うことは、次のとおりです。

1. リリースの `SHA256SUMS` と `manifest.json` を取得し、パッケージを取得して照合します。合わなければ、何も変えずに止まります。
2. 依存関係を確かめます。Ubuntu で解決できないときは、パッケージの一覧を 1 回更新（`apt-get update`）してから、もう一度確認します。それでも解決できなければ、unbound を止める前に止まります。
3. 設定を退避し、パッケージを入れ替えます。
4. `unbound-checkconf`、起動と安定の確認、名前解決の確認を行います。名前解決の確認に失敗したときは、上流との一時的な通信の失敗に備えて、`unbound-control flush_bogus` で検証に失敗した応答のキャッシュを消し、10 秒おきに最大 5 回確認し直します（全体で 1 分ほど）。成功したときは、確認し直した回数を表示します。
5. どれかに失敗したら、元の版と設定へ切り戻します（終了コード 1）。切り戻しにも失敗した場合は、その旨を表示します（終了コード 3）。

`--version` を省くと、公開されている最新版になります。

複数のサーバーがある場合は、1 台で更新して様子を見てから、残りを更新してください。同じ版の不具合は、全台が受けます。

## そのほかのコマンド

| したいこと | コマンド |
| --- | --- |
| 導入済みの版と、公開されている最新版を見る | `sudo unbound-update check` |
| サーバーの状態を確かめる | `sudo unbound-update verify` |
| 直前の版へ手動で戻す | `sudo unbound-update rollback`（パッケージだけを戻し、設定は変えません） |
| `unbound-update` 自体を更新する | `sudo unbound-update self-update` |
| 除外設定とファイアウォールの許可を元に戻す | `sudo unbound-update uninstall` |

サーバーには、直前の版のパッケージを 1 世代分残しています。

## 照合で確かめられること

`unbound-update` は、リリースの `SHA256SUMS` と各ファイルを照合します。確かめられるのは「このリポジトリのリリースにあるものと同じ」ことまでです。リリースそのものが差し替えられた場合は、検出できません。

## 副作用と戻し方

| 変更 | 場所 | 副作用 | 戻し方 |
| --- | --- | --- | --- |
| OS 標準 unbound の除外（Rocky） | `/etc/dnf/dnf.conf` の `[main]` の `excludepkgs` | OS のリポジトリの unbound 関連が `dnf` から見えなくなり、OS 側の修正は自動では入りません | `sudo unbound-update uninstall`。手動なら、その行から unbound 関連の名前を消します |
| OS 標準 unbound の除外（Ubuntu） | `/etc/apt/preferences.d/unbound-packager`（優先度 -1）、`/etc/apt/apt.conf.d/52unbound-packager`（unattended-upgrades の除外） | 同上 | `sudo unbound-update uninstall`。手動なら 2 つのファイルを消します |
| ファイアウォールで DNS を許可 | Rocky: firewalld の dns サービス。Ubuntu: ufw が有効な場合だけ 53 | 53 番への接続を受け付けます | `sudo unbound-update uninstall`（この仕組みが開けた場合だけ閉じます） |
| 統一したビルドオプション | パッケージ | `--disable-sha1` により、SHA-1 系の署名（アルゴリズム 5 と 7）だけのゾーンは、検証済みではなく未署名として扱われます。名前解決はできますが、AD フラグは付きません | OS 標準のパッケージへ戻すか、`packaging/options.json` を変えて作り直します |
| Rocky の既定設定の変更 | パッケージ | `module-config` から `ipsecmod` が外れ、TLS は OS の暗号ポリシーに従わなくなります。`harden-referral-path` が `no`（上流と Ubuntu の既定）になり、参照の途中のネームサーバー情報の追加の検証を行いません | 同上 |
| GitHub への外向きの接続 | DNS サーバー | 更新のたびに、GitHub へ HTTPS で接続します | 使わなければ接続しません |

`uninstall` は、導入済みの unbound のパッケージと設定を残します。

## うまくいかないとき

| 症状 | 原因と対処 |
| --- | --- |
| `別の unbound-update が実行中です`（終了コード 75） | 同じサーバーで、もう 1 つ動いています。終わるのを待ちます |
| `SHA-256 が一致しません` | 取得したファイルが `SHA256SUMS` と違います。導入しません。時間をおいてやり直し、続くならリリースを確かめてください |
| `取得できませんでした` | GitHub へ接続できません。サーバーから `curl -I https://github.com` で確かめます |
| `まだこの仕組みで導入した版がありません` | 最初は `update` ではなく `migrate` を実行します |
| `依存関係を解決できません` | OS が古い可能性があります。`dnf update` や `apt upgrade` のあとでやり直します |
| `対応していない OS です` | Rocky Linux 10 と Ubuntu 26.04 だけに対応しています |
