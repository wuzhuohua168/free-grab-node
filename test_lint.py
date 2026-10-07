"""Generator: build subscription configs from raw node data."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import Any


def to_clash(nodes: list[dict[str, Any]]) -> str:
    """Render nodes as Clash YAML configuration."""
    lines = ["proxies:"]
    for node in nodes:
        name = node.get("name", "unnamed")
        lines.append(f"  - name: {name}")
    return "\n".join(lines) + "\n"


def to_v2ray(nodes: list[dict[str, Any]]) -> str:
    """Render nodes as vmess base64 encoded strings."""
    encoded = []
    for node in nodes:
        vmess = {
            "v": "2",
            "ps": node.get("name", "unnamed"),
            "add": node.get("host", "127.0.0.1"),
            "port": node.get("port", 443),
        }
        raw = json.dumps(vmess, ensure_ascii=False)
        encoded.append(base64.b64encode(raw.encode()).decode())
    decoded = "\n".join(encoded)
    return decoded + "\n"


def main() -> None:
    """Entry point."""
    nodes = []
    print(f"Generated {len(nodes)} nodes")


if __name__ == "__main__":
    main()