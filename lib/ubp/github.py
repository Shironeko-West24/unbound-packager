import json
import urllib.error
import urllib.request

from .util import UbpError

API = "https://api.github.com"
BOT = "github-actions[bot]"
ISSUE_PREFIX = "ビルド失敗: Unbound "


def http_transport(token):
    def call(method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "unbound-packager",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, OSError) as exc:
            raise UbpError(f"GitHub の API に接続できませんでした（{type(exc).__name__}）。")

    return call


class GitHub:
    def __init__(self, repository, transport):
        self.repo = repository
        self.call = transport

    def _get(self, path):
        status, data = self.call("GET", f"/repos/{self.repo}{path}")
        if status != 200:
            raise UbpError(f"GitHub の API が失敗しました（HTTP {status}）: {path.split('?')[0]}")
        return data

    def release_tags(self):
        return [item["tag_name"] for item in self._get("/releases?per_page=100") if not item.get("draft")]

    def failure_issue(self, version):
        title = ISSUE_PREFIX + version
        for issue in self._get("/issues?state=open&creator=" + BOT.replace("[", "%5B").replace("]", "%5D") + "&per_page=100"):
            if issue.get("title") == title and issue.get("user", {}).get("login") == BOT and "pull_request" not in issue:
                return issue
        return None

    def attempts(self, version):
        issue = self.failure_issue(version)
        if not issue:
            return 0
        comments = self._get(f"/issues/{issue['number']}/comments?per_page=100")
        return 1 + sum(1 for item in comments if item.get("user", {}).get("login") == BOT)

    def record_failure(self, version, body):
        issue = self.failure_issue(version)
        if issue:
            status, _ = self.call("POST", f"/repos/{self.repo}/issues/{issue['number']}/comments", {"body": body})
        else:
            status, _ = self.call("POST", f"/repos/{self.repo}/issues", {"title": ISSUE_PREFIX + version, "body": body})
        if status not in (200, 201):
            raise UbpError(f"失敗の記録を Issue に書けませんでした（HTTP {status}）。")
        return self.attempts(version)

    def close_failure(self, version, body):
        issue = self.failure_issue(version)
        if not issue:
            return False
        self.call("POST", f"/repos/{self.repo}/issues/{issue['number']}/comments", {"body": body})
        self.call("PATCH", f"/repos/{self.repo}/issues/{issue['number']}", {"state": "closed"})
        return True

    def enable_workflow(self, name):
        status, _ = self.call("PUT", f"/repos/{self.repo}/actions/workflows/{name}/enable")
        return status == 204
