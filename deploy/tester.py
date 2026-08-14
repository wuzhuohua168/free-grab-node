#!/usr/bin/env python3
"""deploy/tester.py - 电信/移动线路节点连通性测试脚本

通过 mihomo RESTful API 从中国运营商线路测试每个代理节点的真实延迟。
输出结果供 generator.py 合并使用。
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote
import requests

API = "http://127.0.0.1:9090"
TIMEOUT_S = 8
WORKERS = 8

# 测试目标：普通连通 + AI服务
TARGETS = {
    "connectivity": "http://www.gstatic.com/generate_204",
    "google": "https://www.google.com/generate_204",
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
}


def get_proxies() -> dict[str, dict]:
    """获取 mihomo 中的所有代理节点"""
    resp = requests.get(f"{API}/proxies", timeout=5)
    return resp.json().get("proxies", {})


def test_node(name: str, target_url: str) -> int | None:
    """通过代理节点请求目标 URL，返回延迟(ms)"""
    try:
        r = requests.get(
            f"{API}/proxies/{quote(name, safe='')}/delay",
            params={"url": target_url, "timeout": 5000},
            timeout=TIMEOUT_S,
        )
        if r.status_code != 200:
            return None
        delay = r.json().get("delay", 0)
        return delay if 0 < delay <= 5000 else None
    except Exception:
        return None


def test_all_nodes(carrier: str) -> dict:
    """测试所有节点"""
    proxies = get_proxies()
    proxy_list = {
        name: info
        for name, info in proxies.items()
        if info.get("type") in ("Vmess", "Trojan", "Shadowsocks", "Vless", "Http", "Socks5")
    }
    print(f"[{carrier}] 待测节点: {len(proxy_list)}")

    results: dict[str, dict[str, int | None]] = {}
    futures_map = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        for name in proxy_list:
            for label, url in TARGETS.items():
                key = (name, label)
                futures_map[executor.submit(test_node, name, url)] = key

        for completed, future in enumerate(as_completed(futures_map), start=1):
            name, label = futures_map[future]
            try:
                delay = future.result()
            except Exception:
                delay = None
            results.setdefault(name, {})[label] = delay
            if completed % 20 == 0:
                passed = sum(1 for v in results.values() if v.get("connectivity"))
                print(f"[{carrier}] 测试进度: {completed}/{len(futures_map)} 连通={passed}")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--carrier", default="unknown", help="运营商标识: telecom/mobile")
    parser.add_argument("--output", default="deploy/results.json", help="输出文件路径")
    args = parser.parse_args()

    start = time.time()
    results = test_all_nodes(args.carrier)
    elapsed = int(time.time() - start)

    output = {
        "carrier": args.carrier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "nodes_tested": len(results),
        "nodes_connected": sum(1 for v in results.values() if v.get("connectivity")),
        "results": {name: vals for name, vals in results.items() if any(v is not None for v in vals.values())},
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[{args.carrier}] 测试完成: {output['nodes_connected']}/{output['nodes_tested']} 连通, 耗时 {elapsed}s")


if __name__ == "__main__":
    main()