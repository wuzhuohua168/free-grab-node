#!/usr/bin/env python3
"""
免费代理节点提取和聚合系统
自动从多个源提取免费代理节点，进行延迟测试，生成Clash配置文件
"""

from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests
import yaml

VERSION = "v1.0"
CLASH_OUTPUT = Path("output/clash.yaml")
ROCKET_OUTPUT = Path("output/rocket.txt")
TEST_URL = "http://www.gstatic.com/generate_204"
SOURCE_TIMEOUT = 25
LATENCY_TIMEOUT_MS = 6000
MAX_RETRIES = 3
MAX_WORKERS = int(os.getenv("FREE_PROXY_MAX_WORKERS", "12"))

# 节点源配置
SOURCE_GROUPS = [
    {
        "name": "ermaozi clash",
        "url": "https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/clash.yml",
    },
    {
        "name": "anaer Sub",
        "url": "https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml",
    },
    {
        "name": "aiboboxx clashfree",
        "url": "https://raw.githubusercontent.com/aiboboxx/clashfree/main/clash.yml",
    },
    {
        "name": "vxiaov free_proxies",
        "url": "https://raw.githubusercontent.com/vxiaov/free_proxies/main/clash/clash.provider.yaml",
    },
    {
        "name": "chengaopan AutoMerge",
        "url": "https://raw.githubusercontent.com/chengaopan/AutoMergePublicNodes/master/list.yml",
    },
    {
        "name": "mahdibland Aggregator",
        "url": "https://raw.githubusercontent.com/mahdibland/ShadowsocksAggregator/master/Eternity.yml",
    },
]

# 支持的代理类型
SUPPORTED_PROXY_TYPES = {
    "ss",
    "ssr",
    "vmess",
    "vless",
    "trojan",
    "hysteria",
    "hysteria2",
    "hy2",
    "tuic",
    "socks5",
    "http",
}


@dataclass
class ProxyMetric:
    """代理节点度量数据"""
    proxy: dict[str, Any]
    latency: int
    region: str
    health_score: float


def fetch_text(url: str, retries: int = MAX_RETRIES) -> str:
    """从URL获取文本内容"""
    headers = {
        "User-Agent": f"free-proxy-airport/{VERSION}",
        "Accept": "text/plain, text/yaml, application/yaml, */*",
    }
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=SOURCE_TIMEOUT)
            response.raise_for_status()
            return response.content.decode("utf-8", errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 * attempt)

    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def maybe_base64_decode(text: str) -> str:
    """尝试Base64解码"""
    compact = "".join(text.split())
    if not compact or len(compact) % 4 != 0:
        return text
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
        return text
    try:
        decoded = base64.b64decode(compact, validate=True).decode("utf-8")
    except Exception:
        return text
    return decoded if "proxies:" in decoded or "://" in decoded else text


def load_yaml_document(text: str) -> Any:
    """加载YAML文档"""
    try:
        return yaml.safe_load(maybe_base64_decode(text))
    except yaml.YAMLError as exc:
        print(f"[WARN] YAML解析失败: {exc}")
        return None


def extract_proxies(text: str) -> list[dict[str, Any]]:
    """从文本中提取代理节点"""
    document = load_yaml_document(text)

    if isinstance(document, dict):
        proxies = document.get("proxies", [])
    elif isinstance(document, list):
        proxies = document
    else:
        proxies = []

    clean: list[dict[str, Any]] = []
    for proxy in proxies:
        if isinstance(proxy, dict):
            clean.append(dict(proxy))

    return clean


def collect_proxies() -> tuple[int, list[dict[str, Any]]]:
    """从所有源收集代理节点"""
    collected: list[dict[str, Any]] = []

    for source in SOURCE_GROUPS:
        try:
            text = fetch_text(source["url"])
            found = extract_proxies(text)
            print(f"[OK] source={source['name']} proxies={len(found)}")

            if found:
                collected.extend(found)
        except Exception as exc:
            print(f"[WARN] source={source['name']} skipped, error={exc}")

    sanitized = sanitize_and_deduplicate(collected)
    return len(collected), sanitized


def sanitize_and_deduplicate(proxies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """清理并去重代理节点"""
    seen_fingerprints: set[str] = set()
    seen_names: set[str] = set()
    result: list[dict[str, Any]] = []

    for index, raw in enumerate(proxies, start=1):
        proxy = normalize_proxy(raw, index)
        if not proxy:
            continue

        fingerprint = proxy_fingerprint(proxy)
        if fingerprint in seen_fingerprints:
            continue

        seen_fingerprints.add(fingerprint)
        base_name = str(proxy["name"]).strip() or f"node-{index}"
        name = base_name
        suffix = 2

        while name in seen_names:
            name = f"{base_name}-{suffix}"
            suffix += 1

        proxy["name"] = name
        seen_names.add(name)
        result.append(proxy)

    return result


def normalize_proxy(raw: dict[str, Any], index: int) -> dict[str, Any] | None:
    """标准化代理节点"""
    proxy = {key: value for key, value in raw.items() if value is not None}
    proxy_type = str(proxy.get("type", "")).lower().strip()

    if proxy_type not in SUPPORTED_PROXY_TYPES:
        return None

    if proxy_type == "hy2":
        proxy_type = "hysteria2"

    proxy["type"] = proxy_type
    name = str(proxy.get("name", "")).strip() or f"node-{index}"
    server = str(proxy.get("server", "")).strip()

    if not server:
        return None

    try:
        port = int(proxy.get("port"))
    except Exception:
        return None

    if port <= 0 or port > 65535:
        return None

    proxy["name"] = name
    proxy["server"] = server
    proxy["port"] = port

    return proxy


def proxy_fingerprint(proxy: dict[str, Any]) -> str:
    """生成代理节点指纹"""
    important = {
        "type": proxy.get("type"),
        "server": proxy.get("server"),
        "port": proxy.get("port"),
        "uuid": proxy.get("uuid"),
        "password": proxy.get("password"),
        "cipher": proxy.get("cipher"),
        "network": proxy.get("network"),
    }
    payload = json.dumps(important, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_region(proxy: dict[str, Any]) -> str:
    """检测代理节点地区"""
    name = str(proxy.get("name", "")).upper()

    # 根据节点名称识别地区
    if any(keyword in name for keyword in ["香港", "HK", "HONGKONG", "HONG KONG"]):
        return "HK"
    elif any(keyword in name for keyword in ["日本", "JP", "JAPAN", "东京", "TOKYO"]):
        return "JP"
    elif any(keyword in name for keyword in ["美国", "US", "USA", "UNITED STATES"]):
        return "US"
    elif any(keyword in name for keyword in ["台湾", "TW", "TAIWAN"]):
        return "TW"
    elif any(keyword in name for keyword in ["新加坡", "SG", "SINGAPORE"]):
        return "SG"
    elif any(keyword in name for keyword in ["韩国", "KR", "KOREA", "首尔", "SEOUL"]):
        return "KR"
    else:
        return "OTHER"


def test_proxy_delay(proxy: dict[str, Any]) -> int:
    """测试代理节点TCP连通性"""
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        start = time.time()
        sock.connect((server, port))
        latency = int((time.time() - start) * 1000)
        sock.close()
        return latency
    except Exception:
        return 9999


def benchmark_proxies(proxies: list[dict[str, Any]]) -> list[ProxyMetric]:
    """测试所有代理节点延迟"""
    if not proxies:
        return []

    print(f"[INFO] 开始测试 {len(proxies)} 个节点的延迟...")
    metrics: list[ProxyMetric] = []

    workers = max(1, min(MAX_WORKERS, len(proxies)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(test_proxy_delay, proxy): proxy
            for proxy in proxies
        }

        for completed, future in enumerate(as_completed(futures), start=1):
            proxy = futures[future]
            try:
                latency = future.result()
                region = detect_region(proxy)

                # 计算健康评分（延迟越低分数越高）
                health_score = max(0, 100 - latency / 20)

                metric = ProxyMetric(
                    proxy=proxy,
                    latency=latency,
                    region=region,
                    health_score=health_score
                )
                metrics.append(metric)

                if completed % 10 == 0:
                    print(f"[INFO] 已测试 {completed}/{len(proxies)} 个节点")
            except Exception as exc:
                print(f"[WARN] 节点测试失败: {proxy.get('name', 'unknown')}, error={exc}")

    # 按健康评分排序
    metrics.sort(key=lambda m: m.health_score, reverse=True)
    print(f"[OK] 完成节点测试，有效节点: {len(metrics)}")

    return metrics


def generate_clash_config(metrics: list[ProxyMetric]) -> dict[str, Any]:
    """生成Clash配置文件"""
    # 过滤掉延迟过高的节点（超过600ms）
    valid_metrics = [m for m in metrics if m.latency < 600]

    if not valid_metrics:
        print("[WARN] 没有有效的代理节点")
        valid_metrics = metrics[:10]  # 至少保留前10个节点

    proxies = [m.proxy for m in valid_metrics]
    proxy_names = [p["name"] for p in proxies]

    # 按地区分组
    hk_proxies = [m.proxy["name"] for m in valid_metrics if m.region == "HK"]
    jp_proxies = [m.proxy["name"] for m in valid_metrics if m.region == "JP"]
    us_proxies = [m.proxy["name"] for m in valid_metrics if m.region == "US"]
    ai_proxies = hk_proxies[:5] + jp_proxies[:5] + us_proxies[:5]

    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "bind-address": "*",
        "mode": "rule",
        "log-level": "info",
        "ipv6": False,
        "external-controller": "127.0.0.1:9090",

        "proxies": proxies,

        "proxy-groups": [
            {
                "name": "PROXY",
                "type": "select",
                "proxies": ["AUTO-FAST", "FALLBACK"] + proxy_names,
            },
            {
                "name": "AUTO-FAST",
                "type": "url-test",
                "proxies": proxy_names[:50] or proxy_names,
                "url": TEST_URL,
                "interval": 300,
                "tolerance": 50,
            },
            {
                "name": "FALLBACK",
                "type": "fallback",
                "proxies": proxy_names or ["DIRECT"],
                "url": TEST_URL,
                "interval": 300,
            },
        ],

        "rules": [
            # AI服务规则
            "DOMAIN-SUFFIX,openai.com,AI-POOL",
            "DOMAIN-SUFFIX,chatgpt.com,AI-POOL",
            "DOMAIN-SUFFIX,claude.ai,AI-POOL",
            "DOMAIN-SUFFIX,anthropic.com,AI-POOL",
            "DOMAIN-SUFFIX,bard.google.com,AI-POOL",

            # Google服务
            "DOMAIN-SUFFIX,google.com,PROXY",
            "DOMAIN-SUFFIX,googleapis.com,PROXY",
            "DOMAIN-SUFFIX,gstatic.com,PROXY",

            # GitHub
            "DOMAIN-SUFFIX,github.com,PROXY",
            "DOMAIN-SUFFIX,githubusercontent.com,PROXY",

            # 国内直连
            "DOMAIN-SUFFIX,cn,DIRECT",
            "DOMAIN-KEYWORD,baidu,DIRECT",
            "DOMAIN-KEYWORD,taobao,DIRECT",
            "DOMAIN-KEYWORD,alipay,DIRECT",
            "DOMAIN-KEYWORD,tmall,DIRECT",
            "DOMAIN-KEYWORD,jd.com,DIRECT",
            "DOMAIN-KEYWORD,bilibili,DIRECT",
            "DOMAIN-KEYWORD,163.com,DIRECT",
            "DOMAIN-KEYWORD,qq.com,DIRECT",
            "DOMAIN-KEYWORD,weixin,DIRECT",

            # GeoIP规则
            "GEOIP,CN,DIRECT",

            # 最终匹配
            "MATCH,PROXY",
        ],
    }

    # 添加地区分组
    if hk_proxies:
        config["proxy-groups"].append({
            "name": "HK-POOL",
            "type": "url-test",
            "proxies": hk_proxies,
            "url": TEST_URL,
            "interval": 300,
        })

    if jp_proxies:
        config["proxy-groups"].append({
            "name": "JP-POOL",
            "type": "url-test",
            "proxies": jp_proxies,
            "url": TEST_URL,
            "interval": 300,
        })

    if us_proxies:
        config["proxy-groups"].append({
            "name": "US-POOL",
            "type": "url-test",
            "proxies": us_proxies,
            "url": TEST_URL,
            "interval": 300,
        })

    if ai_proxies:
        config["proxy-groups"].append({
            "name": "AI-POOL",
            "type": "url-test",
            "proxies": ai_proxies,
            "url": TEST_URL,
            "interval": 300,
        })
        # 更新PROXY组的选项
        config["proxy-groups"][0]["proxies"] = ["AUTO-FAST", "FALLBACK", "AI-POOL", "HK-POOL", "JP-POOL", "US-POOL"] + proxy_names

    return config


def proxy_to_uri(proxy: dict[str, Any]) -> str:
    """将代理节点转换为 Shadowrocket URI 格式"""
    proxy_type = str(proxy.get("type", "")).lower().strip()
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    name = str(proxy.get("name", ""))

    if proxy_type == "ss":
        return _ss_to_uri(proxy)
    elif proxy_type == "ssr":
        return _ssr_to_uri(proxy)
    elif proxy_type == "vmess":
        return _vmess_to_uri(proxy)
    elif proxy_type == "vless":
        return _vless_to_uri(proxy)
    elif proxy_type == "trojan":
        return _trojan_to_uri(proxy)
    elif proxy_type in ("hysteria", "hysteria2", "hy2"):
        return _hysteria_to_uri(proxy)
    elif proxy_type == "tuic":
        return _tuic_to_uri(proxy)
    elif proxy_type == "http":
        username = proxy.get("username", "")
        password = proxy.get("password", "")
        auth = f"{username}:{password}@" if username else ""
        return f"http://{auth}{server}:{port}#{name}"
    elif proxy_type == "socks5":
        username = proxy.get("username", "")
        password = proxy.get("password", "")
        auth = f"{username}:{password}@" if username else ""
        return f"socks5://{auth}{server}:{port}#{name}"
    return ""


def _ss_to_uri(proxy: dict[str, Any]) -> str:
    """Shadowsocks -> ss:// URI"""
    cipher = str(proxy.get("cipher", "aes-256-gcm"))
    password = str(proxy.get("password", ""))
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    name = str(proxy.get("name", ""))

    # ss://base64(method:password)@server:port
    userinfo = base64.b64encode(f"{cipher}:{password}".encode()).decode().rstrip("=")
    return f"ss://{userinfo}@{server}:{port}#{name}"


def _ssr_to_uri(proxy: dict[str, Any]) -> str:
    """ShadowsocksR -> ssr:// URI"""
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    protocol = str(proxy.get("protocol", "origin"))
    method = str(proxy.get("cipher", "aes-256-cfb"))
    obfs = str(proxy.get("obfs", "plain"))
    password = str(proxy.get("password", ""))
    name = str(proxy.get("name", ""))

    # ssr://base64(server:port:protocol:method:obfs:base64pass/?params)
    pass_b64 = base64.b64encode(password.encode()).decode().rstrip("=")
    obfs_param = str(proxy.get("obfs-param", ""))
    protocol_param = str(proxy.get("protocol-param", ""))

    query = []
    if obfs_param:
        query.append(f"obfs-param={obfs_param}")
    if protocol_param:
        query.append(f"protocol-param={protocol_param}")
    if name:
        query.append(f"group={name}")
    query_str = "?" + "&".join(query) if query else ""

    main = f"{server}:{port}:{protocol}:{method}:{obfs}:{pass_b64}/{query_str}"
    return "ssr://" + base64.b64encode(main.encode()).decode().rstrip("=")


def _vmess_to_uri(proxy: dict[str, Any]) -> str:
    """VMess -> vmess:// URI"""
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    uuid = str(proxy.get("uuid", ""))
    name = str(proxy.get("name", ""))

    config = {
        "v": "2",
        "ps": name,
        "add": server,
        "port": str(port),
        "id": uuid,
        "aid": str(proxy.get("alterId", 0)),
        "scy": str(proxy.get("cipher", "auto")),
        "net": str(proxy.get("network", "tcp")),
        "type": str(proxy.get("type", "none")),
        "host": str(proxy.get("host", proxy.get("ws-opts", {}).get("headers", {}).get("Host", "") if isinstance(proxy.get("ws-opts"), dict) else "")),
        "path": str(proxy.get("path", proxy.get("ws-opts", {}).get("path", "/") if isinstance(proxy.get("ws-opts"), dict) else "/")),
        "tls": str(proxy.get("tls", "")),
        "sni": str(proxy.get("sni", proxy.get("servername", ""))),
        "alpn": str(proxy.get("alpn", "")),
        "fp": str(proxy.get("fp", proxy.get("fingerprint", ""))),
    }
    return "vmess://" + base64.b64encode(json.dumps(config, separators=(",", ":")).encode()).decode()


def _vless_to_uri(proxy: dict[str, Any]) -> str:
    """VLESS -> vless:// URI"""
    uuid = str(proxy.get("uuid", ""))
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    name = str(proxy.get("name", ""))

    params = []
    params.append(f"type={proxy.get('network', 'tcp')}")
    params.append(f"security={proxy.get('tls', 'none')}")
    if proxy.get("tls") == "reality":
        params.append(f"flow={proxy.get('flow', '')}")
        params.append(f"pbk={proxy.get('pbk', '')}")
        params.append(f"sid={proxy.get('sid', '')}")
    if proxy.get("network") == "ws":
        params.append(f"path={proxy.get('path', '/')}")
        params.append(f"host={proxy.get('host', '')}")
    if proxy.get("sni"):
        params.append(f"sni={proxy.get('sni')}")
    params.append(f"encryption={proxy.get('encryption', 'none')}")
    params.append(f"fp={proxy.get('fp', proxy.get('fingerprint', ''))}")

    return f"vless://{uuid}@{server}:{port}?{'&'.join(params)}#{name}"


def _trojan_to_uri(proxy: dict[str, Any]) -> str:
    """Trojan -> trojan:// URI"""
    password = str(proxy.get("password", ""))
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    name = str(proxy.get("name", ""))

    params = []
    if proxy.get("sni"):
        params.append(f"sni={proxy.get('sni')}")
    if proxy.get("alpn"):
        params.append(f"alpn={proxy.get('alpn')}")
    params.append(f"allowInsecure=1")

    query = "?" + "&".join(params) if params else ""
    return f"trojan://{password}@{server}:{port}{query}#{name}"


def _hysteria_to_uri(proxy: dict[str, Any]) -> str:
    """Hysteria/Hysteria2 -> hysteria2:// URI"""
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    name = str(proxy.get("name", ""))

    params = []
    if proxy.get("insecure"):
        params.append("insecure=1")
    if proxy.get("sni"):
        params.append(f"sni={proxy.get('sni')}")

    query = "?" + "&".join(params) if params else ""

    auth = proxy.get("auth", proxy.get("password", ""))
    if isinstance(auth, str) and auth:
        return f"hysteria2://{auth}@{server}:{port}{query}#{name}"
    return f"hysteria2://{server}:{port}{query}#{name}"


def _tuic_to_uri(proxy: dict[str, Any]) -> str:
    """TUIC -> tuic:// URI"""
    uuid = str(proxy.get("uuid", ""))
    password = str(proxy.get("password", ""))
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    name = str(proxy.get("name", ""))

    params = []
    params.append(f"congestion_control={proxy.get('congestion_control', 'cubic')}")
    params.append(f"alpn={proxy.get('alpn', 'h3')}")
    if proxy.get("sni"):
        params.append(f"sni={proxy.get('sni')}")
    params.append("allowInsecure=1")

    return f"tuic://{uuid}:{password}@{server}:{port}?{'&'.join(params)}#{name}"


def generate_shadowrocket_sub(proxies: list[dict[str, Any]]) -> str:
    """生成 Shadowrocket 订阅内容（base64编码的URI列表）"""
    uris = []
    for proxy in proxies:
        uri = proxy_to_uri(proxy)
        if uri:
            uris.append(uri)

    plaintext = "\n".join(uris)
    return base64.b64encode(plaintext.encode("utf-8")).decode()


def main() -> None:
    """主函数"""
    print(f"=== Free Proxy Grab Node {VERSION} ===")
    print(f"开始时间: {datetime.now(timezone.utc).isoformat()}")

    # 收集代理节点
    total_collected, proxies = collect_proxies()
    print(f"[OK] 收集到 {total_collected} 个节点，去重后 {len(proxies)} 个")

    if not proxies:
        print("[ERROR] 没有找到任何代理节点")
        return

    # 测试节点延迟
    metrics = benchmark_proxies(proxies)

    # 生成Clash配置
    config = generate_clash_config(metrics)

    # 确保输出目录存在
    CLASH_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # 写入Clash YAML配置文件
    with CLASH_OUTPUT.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    print(f"[OK] Clash配置已生成: {CLASH_OUTPUT}")

    # 生成Shadowrocket订阅（过滤超时节点，按健康评分排序）
    # 只保留TCP连通成功的节点（延迟 < 600ms）
    valid_metrics = [m for m in metrics if m.latency < 600]
    print(f"[INFO] TCP连通: {len(valid_metrics)}/{len(metrics)} 个节点")

    # 按健康评分排序，取前500个节点
    valid_metrics.sort(key=lambda m: m.health_score, reverse=True)
    rocket_proxies = [m.proxy for m in valid_metrics[:500]]
    rocket_content = generate_shadowrocket_sub(rocket_proxies)
    with ROCKET_OUTPUT.open("w", encoding="utf-8") as f:
        f.write(rocket_content)

    print(f"[OK] Shadowrocket订阅已生成: {ROCKET_OUTPUT} ({len(rocket_proxies)} 节点)")
    print(f"完成时间: {datetime.now(timezone.utc).isoformat()}")

    # 输出统计信息
    region_stats = {}
    for m in metrics:
        region_stats[m.region] = region_stats.get(m.region, 0) + 1

    print("\n=== 节点地区分布 ===")
    for region, count in sorted(region_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"{region}: {count}")

    # 输出URI统计
    uri_count = len([p for p in proxies if proxy_to_uri(p)])
    print(f"\n=== 订阅统计 ===")
    print(f"Clash节点: {len(config.get('proxies', []))}")
    print(f"Shadowrocket节点: {uri_count}")


if __name__ == "__main__":
    main()