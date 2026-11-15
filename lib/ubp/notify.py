import datetime
import json
import urllib.request

from .util import add_secret, log

COLORS = {"info": 0x3498DB, "success": 0x2ECC71, "warning": 0xF1C40F, "error": 0xE74C3C}


class Notifier:
    def __init__(self, webhook_url="", sender=None):
        self.url = webhook_url or ""
        add_secret(self.url)
        self.sender = sender or self._post
        self.sent = []

    def _post(self, url, payload):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "unbound-packager/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()

    def send(self, severity, title, body=""):
        payload = {
            "embeds": [
                {
                    "title": title[:250],
                    "description": body[:3900],
                    "color": COLORS[severity],
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "footer": {"text": "unbound-packager"},
                }
            ]
        }
        self.sent.append((severity, title, body))
        log(f"通知［{severity}］{title}")
        if not self.url:
            return False
        try:
            self.sender(self.url, payload)
            return True
        except Exception as exc:
            log(f"通知を送れませんでした（{type(exc).__name__}）。処理は続けます。")
            return False


def new_version(version, run_url):
    return ("info", f"Unbound の新しい版 {version} を検知しました", f"ビルドとテストを始めます。\n{run_url}")


def released(version, tag, summary, unpackaged, release_url):
    body = (
        f"版 {version} のビルドとテストに合格し、公開しました（{tag}）。\n\n{summary}\n\n"
        "更新するには、各 DNS サーバーで次を実行してください（1 台で確かめてから、残りへ進めます）。\n"
        f"```\nsudo unbound-update update --version {version}\n```\n{release_url}"
    )
    if unpackaged:
        listing = "\n".join(unpackaged[:30])
        body += f"\n\n上流で増えた可能性があり、パッケージに入っていないファイルがあります。\n```\n{listing}\n```"
    return ("success", f"Unbound {version} を公開しました", body)


def failed(version, stage, detail, attempt, max_attempts, run_url):
    remaining = max_attempts - attempt
    tail = "次の実行で、もう一度試します。" if remaining > 0 else "自動の再試行は終わりました。原因を調べてください。"
    return (
        "error",
        f"Unbound {version} の{stage}に失敗しました（{attempt}/{max_attempts} 回目）",
        f"{detail[-1200:]}\n\n{tail}\n{run_url}",
    )


def gave_up(version, max_attempts):
    return (
        "warning",
        f"Unbound {version} の自動の再試行を終了しました",
        f"{max_attempts} 回失敗しました。次の新しい版が出るか、手動で実行するまで、自動では試しません。",
    )


def heartbeat(upstream, published):
    return (
        "info",
        "定期実行は動いています（月次の確認）",
        f"上流の最新版: {upstream}\n公開済みの最新: {published or 'なし'}\nこの通知が毎月 1 日に届かなければ、定期実行が止まっています。",
    )
