#!/usr/bin/env python3
"""
parse_bad_nodes.py — 从 GitHub Issues (label=bad-node) 中提取坏节点 server:port，写入 blocked.txt。
在 CI 中先于 generator.py 执行: generator.py 启动时读取 blocked.txt 自动过滤。
"""
import os
import re
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

OWNER_REPO = os.getenv("OWNER_REPO", "wuzhuohua168/free-grab-node")
TOKEN = os.getenv("GITHUB_TOKEN", "")
BLOCKED_PATH = Path("blocked.txt")
ISSUE_LABEL = "bad-node"

# 匹配 server:port 的正则（支持 IPv4、IPv6[::1]、域名）
PORT_RE = re.compile(
    r"(?:\[([0-9a-fA-F:\.]+)\]|([\w][\w.\-]*))"  # [IPv6] 或 domain/IPv4
    r":(\d{1,5})"
)


def api_get(path: str) -> list[dict]:
    """GET /repos/.../issues?labels=bad-node&state=open&per_page=100"""
    url = f"https://api.github.com/{path}"
    hdrs = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    req = urllib.request.Request(url, headers=hdrs)
    data: list[dict] = []
    page = 1
    while True:
        paged_url = f"{url}&page={page}&per_page=100" if "?" in url else f"{url}?page={page}&per_page=100"
        req = urllib.request.Request(paged_url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                items = json.loads(resp.read().decode("utf-8"))
                if not items:
                    break
                data.extend(items)
                page += 1
                if len(items) < 100:
                    break
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
            break
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            break
    return data


def extract_server_ports(text: str) -> set[str]:
    """从文本中提取所有 server:port 组合，去重归一化。"""
    blocked: set[str] = set()
    for match in PORT_RE.finditer(text):
        host_raw = match.group(1) or match.group(2)
        port = match.group(3)
        if not host_raw or not port:
            continue
        host = host_raw.strip().lower()
        port_int = int(port)
        if 1 <= port_int <= 65535:
            blocked.add(f"{host}:{port_int}")
    return blocked


def parse_issue_body(body: str) -> set[str]:
    """解析 Issue YAML 表单中的 'nodes' 字段。"""
    blocked: set[str] = set()
    # YAML 表单输出格式：字段名在标签后，值在下一行或同格式缩进
    # 典型结构：\n### 坏节点地址 (server:port)\n1.2.3.4:443\n
    lines = body.splitlines()
    in_nodes = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### ") and ("坏节点" in stripped or "node" in stripped.lower()):
            in_nodes = True
            continue
        if in_nodes:
            if stripped.startswith("### ") or stripped == "":
                if in_nodes and stripped == "":
                    continue  # 跳过空行，继续找值
                break
            # 这一行是值（可能逗号分隔多个）
            for chunk in re.split(r"[,\n]", stripped):
                chunk = chunk.strip().lstrip("- ")
                if chunk:
                    blocked.update(extract_server_ports(chunk))
    return blocked


def main() -> None:
    if not TOKEN:
        print("[WARN] GITHUB_TOKEN 未设置，跳过 Issue 解析")
        # 确保 blocked.txt 存在（保留已有内容）
        if not BLOCKED_PATH.exists():
            BLOCKED_PATH.write_text("# 坏节点黑名单 (自动维护，勿手动编辑)\n", encoding="utf-8")
        return

    print(f"[INFO] 解析 GitHub Issues (label={ISSUE_LABEL})...")
    issues = api_get(f"repos/{OWNER_REPO}/issues?labels={ISSUE_LABEL}&state=open")
    print(f"[INFO] 找到 {len(issues)} 个 open issues")

    all_blocked: set[str] = set()
    for issue in issues:
        body = str(issue.get("body") or "")
        found = parse_issue_body(body)
        if found:
            print(f"  issue #{issue['number']}: {found}")
            all_blocked.update(found)

    print(f"[INFO] 总计解析到 {len(all_blocked)} 个坏节点")

    # 写入 blocked.txt
    header = "# 坏节点黑名单 (CI 自动维护，每 30 分钟从 GitHub Issues 更新)\n"
    header += "# 格式: 每行一个 server:port (来自 Issues 中标记为 bad-node 的反馈)\n"
    header += f"# 最后更新: 由 parse_bad_nodes.py 在 CI run 中写入\n"
    if all_blocked:
        header += f"# 当前共 {len(all_blocked)} 个坏节点\n"
        content = header + "".join(sorted(all_blocked) + ["\n"])
    else:
        content = header + "\n# 当前为空: 还没有被报告的坏节点\n"

    BLOCKED_PATH.write_text(content, encoding="utf-8")
    print(f"[OK] blocked.txt 已更新: {BLOCKED_PATH}")


if __name__ == "__main__":
    main()
