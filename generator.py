#!/usr/bin/env python3
"""
免费代理节点提取和聚合系统
自动从多个源提取免费代理节点，进行延迟测试，生成Clash配置文件
"""

from __future__ import annotations
import base64
import gzip
import hashlib
import json
import os
import platform
import random
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
import requests
import yaml

VERSION = "v1.3.0"
CLASH_OUTPUT = Path("output/clash.yaml")
ROCKET_OUTPUT = Path("output/rocket.txt")
V2RAY_OUTPUT = Path("output/v2ray.txt")
# 坏节点黑名单(CI 每 30 分钟从 GitHub Issues 自动解析更新): 列出 server:port 直接过滤
BLOCKLIST_PATH = Path("blocked.txt")
# 测速探针:多个目标 URL 交叉验证,避免单 URL 假活(节点对 gstatic 通 ≠ 真实可用)
#  - gstatic generate_204:轻量连通性(原逻辑)
#  - gstatic 首页:完整 HTTP 响应(验证非"只握手不传输")
#  - 1.1.1.1:境外 IP 直连(验证基础出口,无 DNS 依赖)
TEST_URLS = [
    "http://www.gstatic.com/generate_204",
    "https://www.gstatic.com/",
    "https://1.1.1.1/",
]
TEST_URL = TEST_URLS[0]
SOURCE_TIMEOUT = 25
LATENCY_TIMEOUT_MS = 5000
# 丢包探测:同一节点连发多次,统计失败次数(免费节点常"单测能过、并发/多次就挂")
LOSS_PROBE_COUNT = 3          # 每个节点连测次数
LOSS_PROBE_MAX_FAIL = 1       # 允许的最大失败次数(>=2 次失败即判死,比原逻辑更严)
MIN_PASS_URLS = 2             # 多个 URL 中至少几个要通才算过
MAX_RETRIES = 3
MAX_WORKERS = int(os.getenv("FREE_PROXY_MAX_WORKERS", "24"))
MAX_CANDIDATES = int(os.getenv("FREE_PROXY_MAX_CANDIDATES", "0"))  # 0=不限制，与原作者项目一致

# 节点源配置（参考项目 + 用户推荐，多个高质量源）
SOURCE_GROUPS = [
    {
        "name": "openRunner clash-freenode",
        "primary": "https://raw.githubusercontent.com/openRunner/clash-freenode/main/sub.yaml",
        "fallbacks": [
            "https://raw.githubusercontent.com/openRunner/clash-freenode/main/clash.yaml",
            "https://raw.githubusercontent.com/openrunner/clash-freenode/main/clash.yaml",
        ],
    },
    {
        "name": "snakem982 proxypool",
        "primary": "https://raw.githubusercontent.com/snakem982/proxypool/main/clash.yaml",
        "fallbacks": [
            "https://raw.githubusercontent.com/snakem982/proxypool/main/source/clash-meta-2.yaml",
            "https://raw.githubusercontent.com/snakem982/proxypool/main/source/clash-meta.yaml",
        ],
    },
    {
        "name": "Flikify Free-Node",
        "primary": "https://raw.githubusercontent.com/Flikify/Free-Node/main/clash.yaml",
        "fallbacks": [
            "https://raw.githubusercontent.com/a2470982985/getNode/main/clash.yaml",
        ],
    },
    {
        "name": "free-clash-v2ray GitHub Pages",
        "primary": "https://free-clash-v2ray.github.io/uploads/latest.yaml",
        "fallbacks": [
            "discover:free-clash-v2ray",
        ],
    },
    {
        "name": "PuddinCat BestClash",
        "primary": "https://raw.githubusercontent.com/PuddinCat/BestClash/refs/heads/main/proxies.yaml",
        "fallbacks": [],
    },
    {
        "name": "zhuhaiuk free-nodes",
        "primary": "https://raw.githubusercontent.com/zhuhaiuk/free-nodes/main/clash_config.yaml",
        "fallbacks": [],
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

# ---------- 分流规则目录(同步自 node-conversion-tool / RULES.md) ----------
# 37 组远程规则集(blackmatrix7/ios_rule_script),策略 DIRECT / PROXY
RULE_ENTRIES = [
    ("AppleNews", "PROXY"), ("Apple", "DIRECT"), ("BiliBili", "DIRECT"), ("NetEaseMusic", "DIRECT"),
    ("Baidu", "DIRECT"), ("DouBan", "DIRECT"), ("WeChat", "DIRECT"), ("DouYin", "DIRECT"),
    ("Sina", "DIRECT"), ("Zhihu", "DIRECT"), ("XiaoHongShu", "DIRECT"),
    ("YouTube", "PROXY"), ("Netflix", "PROXY"), ("Disney", "PROXY"), ("HBO", "PROXY"),
    ("Spotify", "PROXY"), ("Telegram", "PROXY"), ("PayPal", "PROXY"), ("Twitter", "PROXY"),
    ("Facebook", "PROXY"), ("Amazon", "PROXY"), ("OpenAI", "PROXY"), ("Sony", "DIRECT"),
    ("Nintendo", "DIRECT"), ("Epic", "DIRECT"), ("SteamCN", "DIRECT"), ("Steam", "DIRECT"),
    ("Game", "DIRECT"), ("GitHub", "PROXY"), ("Microsoft", "DIRECT"), ("Google", "PROXY"),
    ("TikTok", "PROXY"), ("TVB", "PROXY"), ("Speedtest", "PROXY"), ("Global", "PROXY"),
    ("China", "DIRECT"), ("Lan", "DIRECT"),
]
BM7_BASE = "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule"

# 内联直连白名单(Apple 认证 + 国内 AI 工具),优先级高于远程规则集,不依赖规则集下载
APPLE_DIRECT_DOMAINS = ["apple.com", "icloud.com", "mzstatic.com", "apple-dns.net"]
DOMESTIC_AI_DIRECT_DOMAINS = [
    "traework.cn", "trae.cn", "workbuddy.cn", "bestvirtualgoods.com",
    "volces.com", "volcengine.com", "deepseek.com", "dashscope.aliyuncs.com",
    "bigmodel.cn", "moonshot.cn", "siliconflow.cn",
]
INLINE_DIRECT_DOMAINS = APPLE_DIRECT_DOMAINS + DOMESTIC_AI_DIRECT_DOMAINS
# 强制走代理域名(预留:确有必须走代理才通的域名时填入,写父域名,DOMAIN-SUFFIX 自动覆盖子域)
FORCE_PROXY_DOMAINS: list[str] = []

# AI 服务走 AI-POOL 策略组(本项目特有,优先级最高)
AI_POOL_RULES = [
    ("openai.com", "AI-POOL"),
    ("chatgpt.com", "AI-POOL"),
    ("claude.ai", "AI-POOL"),
    ("anthropic.com", "AI-POOL"),
]


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


def extract_proxy_block(text: str) -> list[Any]:
    """当 YAML 文档解析失败时，从原始文本中提取 proxies: 块（与原项目一致）"""
    lines = maybe_base64_decode(text).splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if re.match(r"^proxies\s*:\s*$", line):
            start = index
            break
    if start is None:
        return []

    block: list[str] = []
    for line in lines[start + 1:]:
        if line and not line.startswith((" ", "\t", "-")) and re.match(r"^[A-Za-z0-9_-]+\s*:", line):
            break
        block.append(line)

    try:
        parsed = yaml.safe_load("proxies:\n" + "\n".join(block))
    except yaml.YAMLError as exc:
        print(f"[WARN] proxy block parse failed: {exc}")
        return []
    if isinstance(parsed, dict) and isinstance(parsed.get("proxies"), list):
        return parsed["proxies"]
    return []


def extract_proxies(text: str) -> list[dict[str, Any]]:
    """从文本中提取代理节点（与原项目一致：支持 YAML 解析 + proxy block 回退）"""
    document = load_yaml_document(text)

    if isinstance(document, dict):
        proxies = document.get("proxies", [])
    elif isinstance(document, list):
        proxies = document
    else:
        proxies = []

    if not proxies:
        proxies = extract_proxy_block(text)

    clean: list[dict[str, Any]] = []
    for proxy in proxies:
        if isinstance(proxy, dict):
            clean.append(dict(proxy))

    return clean


def collect_proxies() -> tuple[int, list[dict[str, Any]]]:
    """从所有源收集代理节点（支持 primary/fallback）"""
    collected: list[dict[str, Any]] = []

    for source in SOURCE_GROUPS:
        source_found: list[dict[str, Any]] = []
        for url in expand_source_urls(source):
            try:
                text = fetch_text(url)
                found = extract_proxies(text)
                print(f"[OK] source={source['name']} proxies={len(found)} url={url}")
                if found:
                    source_found.extend(found)
                    break
            except Exception as exc:
                print(f"[WARN] source={source['name']} skipped url={url} error={exc}")
        collected.extend(source_found)

    sanitized = sanitize_and_deduplicate(collected)
    return len(collected), sanitized


def expand_source_urls(source: dict[str, Any]) -> list[str]:
    """展开节点源URL（primary + fallbacks）"""
    urls = [str(source["primary"])]
    for item in source.get("fallbacks", []):
        if item == "discover:free-clash-v2ray":
            urls.extend(discover_free_clash_v2ray_urls())
        else:
            urls.append(str(item))
    return _unique_ordered(urls)


def discover_free_clash_v2ray_urls() -> list[str]:
    """从 free-clash-v2ray README 中动态发现最新订阅URL"""
    readme_url = "https://raw.githubusercontent.com/free-clash-v2ray/free-clash-v2ray.github.io/main/README.md"
    try:
        text = fetch_text(readme_url)
    except Exception as exc:
        print(f"[WARN] free-clash-v2ray discovery failed: {exc}")
        return []
    pattern = r"https://free-clash-v2ray\.github\.io/uploads/\d{4}/\d{2}/[0-9]-\d{8}\.yaml"
    return _unique_ordered(re.findall(pattern, text))[:8]


def _unique_ordered(items: list[str]) -> list[str]:
    """保持顺序去重"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


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


def block_key(proxy: dict[str, Any]) -> str:
    """生成节点屏蔽键: 归一化 server:port(域名转小写, 去除首尾点与空格)"""
    server = str(proxy.get("server", "")).strip().lower().rstrip(".")
    port = proxy.get("port")
    return f"{server}:{port}" if port else server


def load_blocklist() -> set[str]:
    """读取 blocked.txt 黑名单(每行一个 server:port, # 开头为注释)"""
    blocked: set[str] = set()
    if not BLOCKLIST_PATH.exists():
        return blocked
    try:
        for line in BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entry = line.lower().rstrip(".")
            if ":" in entry:
                host, _, port = entry.rpartition(":")
                if host and port.isdigit():
                    blocked.add(entry)
            elif re.fullmatch(r"[\w.\-]+", entry):
                blocked.add(entry)
    except Exception as exc:
        print(f"[WARN] 读取黑名单失败: {exc}")
    return blocked


def detect_region(proxy: dict[str, Any]) -> str:
    """检测代理节点地区（正则+emoji+unicode，与参考项目一致）"""
    name = str(proxy.get("name", ""))
    return detect_region_by_name(name)


def detect_region_by_name(name: str) -> str:
    """根据节点名称检测地区"""
    text = name.lower()
    patterns = {
        "HK": (
            "regex:\\bhk\\b",
            "hong kong",
            "\\u9999\\u6e2f",
            "\U0001f1ed\U0001f1f0",
        ),
        "JP": (
            "regex:\\bjp\\b",
            "japan",
            "\\u65e5\\u672c",
            "\U0001f1ef\U0001f1f5",
        ),
        "US": (
            "regex:\\bus\\b",
            "regex:\\busa\\b",
            "united states",
            "america",
            "\\u7f8e\\u56fd",
            "\\u7f8e\\u570b",
            "\U0001f1fa\U0001f1f8",
        ),
        "SG": (
            "regex:\\bsg\\b",
            "singapore",
            "\\u65b0\\u52a0\\u5761",
            "\U0001f1f8\U0001f1ec",
        ),
        "TW": (
            "regex:\\btw\\b",
            "taiwan",
            "\\u53f0\\u6e7e",
            "\U0001f1f9\U0001f1fc",
        ),
        "KR": (
            "regex:\\bkr\\b",
            "korea",
            "\\ud55c\\uad6d",
            "\\u97e9\\u56fd",
            "\\uc11c\\uc6b8",
            "\U0001f1f0\U0001f1f7",
        ),
    }
    for region, tokens in patterns.items():
        for token in tokens:
            if token.startswith("regex:"):
                if re.search(token.removeprefix("regex:"), text):
                    return region
                continue
            if token.startswith("\\u"):
                token = token.encode("utf-8").decode("unicode_escape")
            if token in text:
                return region
    return "OTHER"


def region_bonus(region: str) -> int:
    """地区评分加成（与原项目一致）"""
    if region in {"HK", "SG", "JP"}:
        return 3
    if region == "US":
        return 2
    return 1


def health_score(name: str, latency: int, region: str) -> float:
    """计算节点健康评分（与原项目一致：延迟权重60% + 地区权重30% + 稳定性10%）"""
    stability_seed = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], 16)
    stability = random.Random(stability_seed).random()
    return (1 / latency) * 0.6 + region_bonus(region) * 0.3 + stability * 0.1


# ---- Mihomo 代理引擎测试 ----

def find_free_port() -> int:
    """找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def find_or_install_mihomo() -> Path:
    """查找或安装 mihomo 代理引擎"""
    for name in ("mihomo", "clash-meta", "clash"):
        found = shutil.which(name)
        if found:
            print(f"[OK] using proxy engine: {found}")
            return Path(found)

    install_dir = Path(tempfile.gettempdir()) / "free-grab-node-mihomo"
    install_dir.mkdir(parents=True, exist_ok=True)
    binary = install_dir / ("mihomo.exe" if os.name == "nt" else "mihomo")
    if binary.exists():
        print(f"[OK] using cached proxy engine: {binary}")
        return binary

    url = _select_mihomo_asset()
    print(f"[INFO] downloading proxy engine: {url}")
    archive = _download_file(url, install_dir)
    extracted = _extract_mihomo_binary(archive, install_dir)
    extracted.chmod(extracted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if extracted != binary:
        shutil.copy2(extracted, binary)
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


def _select_mihomo_asset() -> str:
    """选择适合当前系统的 mihomo 二进制"""
    api_url = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
    data = requests.get(api_url, headers={"User-Agent": "free-grab-node"}, timeout=SOURCE_TIMEOUT).json()
    assets = data.get("assets", [])
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_token = "darwin"
    elif system == "linux":
        os_token = "linux"
    elif system == "windows":
        os_token = "windows"
    else:
        raise RuntimeError(f"unsupported OS: {system}")

    arch_tokens = ["amd64-compatible", "amd64"] if machine in {"x86_64", "amd64"} else ["arm64"] if machine in {"arm64", "aarch64"} else [machine]
    if not arch_tokens:
        raise RuntimeError(f"unsupported arch: {machine}")

    candidates: list[tuple[int, str]] = []
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        download_url = str(asset.get("browser_download_url", ""))
        if not download_url or os_token not in name:
            continue
        if not any(t in name for t in arch_tokens):
            continue
        if not (name.endswith(".gz") or name.endswith(".zip")):
            continue
        score = 10 if "compatible" in name else 0
        if "go120" not in name:
            score += 2
        candidates.append((score, download_url))

    if not candidates:
        raise RuntimeError("no matching mihomo release asset found")
    candidates.sort(reverse=True)
    return candidates[0][1]


def _download_file(url: str, directory: Path) -> Path:
    """下载文件"""
    target = directory / Path(url.split("?")[0]).name
    with requests.get(url, stream=True, timeout=SOURCE_TIMEOUT) as response:
        response.raise_for_status()
        with target.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
    return target


def _extract_mihomo_binary(archive: Path, directory: Path) -> Path:
    """解压 mihomo 二进制"""
    if archive.suffix == ".gz" and not archive.name.endswith(".tar.gz"):
        target = directory / archive.name[:-3]
        with gzip.open(archive, "rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return target
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(directory)
        for path in directory.rglob("*"):
            if path.is_file() and "mihomo" in path.name.lower():
                return path
    raise RuntimeError(f"unsupported archive: {archive}")


def _write_benchmark_config(path: Path, proxies: list[dict[str, Any]], controller_port: int) -> None:
    """写入用于基准测试的临时 Clash 配置"""
    names = [str(p["name"]) for p in proxies]
    config = {
        "mixed-port": find_free_port(),
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "external-controller": f"127.0.0.1:{controller_port}",
        "proxies": proxies,
        "proxy-groups": [{"name": "BENCHMARK", "type": "select", "proxies": names or ["DIRECT"]}],
        "rules": ["MATCH,BENCHMARK"],
    }
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _wait_for_controller(controller_url: str, process: subprocess.Popen[str]) -> None:
    """等待 mihomo 控制器就绪"""
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("Mihomo exited before controller became ready")
        try:
            if requests.get(f"{controller_url}/version", timeout=1).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Mihomo controller did not become ready")


def _probe_one(controller_url: str, name: str, url: str, udp: bool = False) -> int | None:
    """对单个 URL 做一次延迟探测,返回延迟(ms)或 None(失败)。udp=True 时走 UDP 探针。"""
    api = f"{controller_url}/proxies/{quote(name, safe='')}/delay"
    params = f"timeout={LATENCY_TIMEOUT_MS}&url={quote(url, safe='')}"
    if udp:
        params += "&udp=true"
    try:
        resp = requests.get(f"{api}?{params}", timeout=(LATENCY_TIMEOUT_MS / 1000) + 3)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        delay = int(resp.json().get("delay", 0))
    except Exception:
        return None
    if delay <= 0 or delay > LATENCY_TIMEOUT_MS:
        return None
    return delay


def _test_single_proxy(controller_url: str, proxy: dict[str, Any]) -> ProxyMetric | None:
    """通过 mihomo 引擎测试单个代理节点。

    零成本增强(不改架构、不依赖国内云):
    1) 多 URL 交叉验证:TEST_URLS 中至少 MIN_PASS_URLS 个通才算过,避免单 URL 假活。
    2) 丢包探测:同一节点连发 LOSS_PROBE_COUNT 次,失败 >= LOSS_PROBE_MAX_FAIL 直接判死。
    3) UDP 探针:hysteria2/tuic 等 UDP 协议额外走 udp=true 探测,否则只验证了 TCP 握手。
    """
    name = str(proxy["name"])
    proxy_type = str(proxy.get("type", "")).lower()

    # --- 多 URL 连通性交叉验证 ---
    passed = 0
    latencies: list[int] = []
    for url in TEST_URLS:
        d = _probe_one(controller_url, name, url)
        if d is not None:
            passed += 1
            latencies.append(d)
    if passed < MIN_PASS_URLS:
        return None

    # --- 丢包探测:对主探测 URL 再发几次,统计失败 ---
    loss_fail = 0
    for _ in range(LOSS_PROBE_COUNT - 1):
        d = _probe_one(controller_url, name, TEST_URL)
        if d is None:
            loss_fail += 1
            if loss_fail >= LOSS_PROBE_MAX_FAIL:
                return None

    # --- UDP 协议额外探针 ---
    if proxy_type in ("hysteria2", "hysteria", "tuic"):
        udp_ok = any(
            _probe_one(controller_url, name, u, udp=True) is not None for u in TEST_URLS
        )
        if not udp_ok:
            return None

    latency = int(sum(latencies) / len(latencies))
    region = detect_region(proxy)
    score = health_score(name, latency, region)
    return ProxyMetric(proxy=proxy, latency=latency, region=region, health_score=score)


def benchmark_proxies(proxies: list[dict[str, Any]]) -> list[ProxyMetric]:
    """使用 mihomo 引擎对代理节点进行真实延迟测试"""
    if not proxies:
        return []

    engine = find_or_install_mihomo()
    with tempfile.TemporaryDirectory(prefix="free-grab-node-") as temp_name:
        temp_dir = Path(temp_name)
        config_path = temp_dir / "benchmark.yaml"
        controller_port = find_free_port()
        controller_url = f"http://127.0.0.1:{controller_port}"
        _write_benchmark_config(config_path, proxies, controller_port)

        process = subprocess.Popen(
            [str(engine), "-d", str(temp_dir), "-f", str(config_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_controller(controller_url, process)
            metrics = _run_delay_tests(controller_url, proxies)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        return metrics


def _run_delay_tests(controller_url: str, proxies: list[dict[str, Any]]) -> list[ProxyMetric]:
    """批量测试所有节点延迟"""
    workers = max(1, min(MAX_WORKERS, len(proxies)))
    metrics: list[ProxyMetric] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_test_single_proxy, controller_url, p): p for p in proxies}
        for completed, future in enumerate(as_completed(futures), start=1):
            proxy = futures[future]
            try:
                metric = future.result()
            except Exception:
                continue
            if metric:
                metrics.append(metric)
            if completed % 25 == 0 or completed == len(futures):
                print(f"[INFO] tested {completed}/{len(futures)} kept={len(metrics)}")
    metrics.sort(key=lambda m: m.health_score, reverse=True)
    print(f"[INFO] mihomo精测完成: {len(metrics)} 个节点通过")
    return metrics


def find_free_port() -> int:
    """找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_meta_header(total_collected: int = 0, kept: int = 0, blocked: int = 0) -> str:
    """生成订阅文件头注释:写入生成时间、测速出口、大陆可达性提示、反馈入口、黑名单统计。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    blocked_line = f"# 众包黑名单: 已屏蔽 {blocked} 个坏节点 (反馈: Issues 模板)\n" if blocked else ""
    return (
        f"# free-grab-node {VERSION} | 生成时间 {now}\n"
        f"# 测速出口: 海外 GitHub Actions (美国) —— 仅验证「节点→海外」可达\n"
        f"# 大陆可达性: 未经本地探针验证(本项目无国内云探针), 导入后请先在客户端测速筛选\n"
        f"# 坏节点反馈: https://github.com/wuzhuohua168/free-grab-node/issues\n"
        f"{blocked_line}"
        f"# 本轮收集 {total_collected} 节点, 去重后 {kept} 节点通过精测\n"
    )


def generate_clash_config(metrics: list[ProxyMetric], meta_total: int = 0) -> dict[str, Any]:
    """生成Clash配置文件(同步自 node-conversion-tool 的完整分流规则 + 抗DNS污染)"""
    metrics.sort(key=lambda m: m.health_score, reverse=True)
    valid_metrics = metrics

    if not valid_metrics:
        print("[WARN] 没有有效的代理节点")
        valid_metrics = metrics[:5]

    proxies = [m.proxy for m in valid_metrics]
    proxy_names = [p["name"] for p in proxies]

    # 按地区分组
    hk_proxies = [m.proxy["name"] for m in valid_metrics if m.region == "HK"]
    jp_proxies = [m.proxy["name"] for m in valid_metrics if m.region == "JP"]
    us_proxies = [m.proxy["name"] for m in valid_metrics if m.region == "US"]
    ai_proxies = hk_proxies[:5] + jp_proxies[:5] + us_proxies[:5]

    # ---- 抗 DNS 污染的 dns 段(同步自 node-conversion-tool) ----
    dns_config = {
        "enable": True,
        "ipv6": False,
        "listen": "0.0.0.0:1053",
        "use-hosts": True,
        "respect-rules": False,
        "default-nameserver": ["223.5.5.5", "119.29.29.29"],
        "proxy-server-nameserver": ["https://dns.quad9.net/dns-query#DIRECT", "223.5.5.5"],
        "direct-nameserver": ["https://dns.quad9.net/dns-query#DIRECT", "223.5.5.5", "119.29.29.29"],
        "enhanced-mode": "fake-ip",
        "fake-ip-range": "198.18.0.1/16",
        "fake-ip-filter": [
            "*.lan", "*.local", "*.localhost",
            "+.msftconnecttest.com", "+.msftncsi.com", "stun.*",
            "+.apple.com", "+.icloud.com", "+.mzstatic.com", "ocsp.apple.com",
            "+.traework.cn", "+.trae.cn", "+.workbuddy.cn",
        ],
        "nameserver": ["223.5.5.5", "119.29.29.29", "https://dns.quad9.net/dns-query#DIRECT"],
        "fallback": ["https://dns.quad9.net/dns-query#PROXY", "tls://9.9.9.9:853#PROXY", "https://dns.google/resolve#PROXY"],
        "fallback-filter": {"geoip": True, "geoip-code": "CN", "domain": ["+.google.com", "+.googleapis.com", "+.gstatic.com", "+.github.com", "+.githubusercontent.com", "+.openai.com", "+.youtube.com", "+.googlevideo.com"]},
    }

    # ---- rule-providers(37 个远程规则集, 每日刷新) ----
    rule_providers = {
        rname: {
            "type": "http",
            "behavior": "classical",
            "url": f"{BM7_BASE}/Clash/{rname}/{rname}.yaml",
            "path": f"./rule-providers/{rname}.yaml",
            "interval": 86400,
        }
        for rname, _ in RULE_ENTRIES
    }

    # ---- 规则列表(顺序: AI优先 > 远程规则集 > 强制代理 > 直连 > GEOIP > 兜底) ----
    rules = [f"DOMAIN-SUFFIX,{d},{policy}" for d, policy in AI_POOL_RULES]
    rules += [f"RULE-SET,{rname},{policy}" for rname, policy in RULE_ENTRIES]
    rules += [f"DOMAIN-SUFFIX,{d},PROXY" for d in FORCE_PROXY_DOMAINS]
    rules += [f"DOMAIN-SUFFIX,{d},DIRECT" for d in INLINE_DIRECT_DOMAINS]
    rules += ["GEOIP,CN,DIRECT", "MATCH,PROXY"]

    config = {
        "mixed-port": 7890,
        "allow-lan": True,
        "bind-address": "*",
        "mode": "rule",
        "log-level": "info",
        "ipv6": True,
        "unified-delay": True,
        "tcp-concurrent": True,
        "global-client-fingerprint": "chrome",
        "external-controller": "127.0.0.1:9090",

        "dns": dns_config,

        "proxies": proxies,

        "proxy-groups": [
            {
                "name": "AUTO-FAST",
                "type": "url-test",
                "proxies": proxy_names,
                "url": TEST_URL,
                "interval": 120,
            },
            {
                "name": "HK-POOL",
                "type": "url-test",
                "proxies": hk_proxies or proxy_names[:5],
                "url": TEST_URL,
                "interval": 120,
            },
            {
                "name": "JP-POOL",
                "type": "url-test",
                "proxies": jp_proxies or proxy_names[:5],
                "url": TEST_URL,
                "interval": 120,
            },
            {
                "name": "US-POOL",
                "type": "url-test",
                "proxies": us_proxies or proxy_names[:5],
                "url": TEST_URL,
                "interval": 120,
            },
            {
                "name": "AI-POOL",
                "type": "url-test",
                "proxies": ai_proxies or proxy_names[:5],
                "url": TEST_URL,
                "interval": 120,
            },
            {
                "name": "FALLBACK",
                "type": "fallback",
                "proxies": ["AUTO-FAST", "HK-POOL", "JP-POOL", "US-POOL"],
                "url": TEST_URL,
                "interval": 120,
            },
            {
                "name": "LOAD-BALANCE",
                "type": "load-balance",
                "strategy": "round-robin",
                "proxies": proxy_names,
            },
            {
                "name": "PROXY",
                "type": "select",
                "proxies": ["AUTO-FAST", "LOAD-BALANCE", "FALLBACK"],
            },
        ],

        "rule-providers": rule_providers,

        "rules": rules,
    }

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

    # 修正 tls 字段：Clash YAML 中 tls 可能是 True/False 布尔值
    tls_val = proxy.get("tls", "")
    if tls_val is True:
        tls_val = "tls"
    elif tls_val is False:
        tls_val = ""

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
        "tls": str(tls_val),
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

    # 修正 tls 字段：Clash YAML 中 tls 可能是 True/False 布尔值
    tls_val = proxy.get("tls", "none")
    if tls_val is True:
        tls_val = "tls"
    elif tls_val is False:
        tls_val = "none"

    params = []
    params.append(f"type={proxy.get('network', 'tcp')}")
    params.append(f"security={tls_val}")
    if tls_val == "reality":
        params.append(f"flow={proxy.get('flow', '')}")
        params.append(f"pbk={proxy.get('pbk', '')}")
        params.append(f"sid={proxy.get('sid', '')}")
    if proxy.get("network") == "ws":
        params.append(f"path={proxy.get('path', '/')}")
        params.append(f"host={proxy.get('host', '')}")
    if proxy.get("sni"):
        sni = str(proxy.get("sni", ""))
        # 清理 sni：移除协议前缀和路径，只保留域名
        sni = sni.replace("https://", "").replace("http://", "")
        sni = sni.split("/")[0].split("#")[0]
        sni = "".join(c for c in sni if ord(c) < 128)  # 去除非ASCII字符
        if sni:
            params.append(f"sni={sni}")
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
        sni = str(proxy.get("sni", ""))
        # 清理 sni：移除协议前缀和路径，只保留域名
        sni = sni.replace("https://", "").replace("http://", "")
        sni = sni.split("/")[0].split("#")[0]
        sni = "".join(c for c in sni if ord(c) < 128)  # 去除非ASCII字符
        if sni:
            params.append(f"sni={sni}")

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
    """主函数（与原项目一致：全节点 mihomo 测试 + 输出全部通过节点）"""
    print(f"=== Free Proxy Grab Node {VERSION} ===")
    print(f"开始时间: {datetime.now(timezone.utc).isoformat()}")

    # 收集代理节点
    total_collected, proxies = collect_proxies()
    print(f"[OK] 收集到 {total_collected} 个节点，去重后 {len(proxies)} 个")

    # 众包黑名单: 过滤被用户反馈为坏节点/大陆不可用的 server:port
    blocklist = load_blocklist()
    if blocklist:
        before = len(proxies)
        proxies = [p for p in proxies if block_key(p) not in blocklist]
        print(f"[OK] 黑名单过滤: 屏蔽 {before - len(proxies)} 个坏节点 (共 {len(blocklist)} 条)")

    # mihomo 真实代理延迟测试（所有节点直接进 mihomo，与原项目一致）
    metrics: list[ProxyMetric] = []
    if proxies:
        try:
            metrics = benchmark_proxies(proxies)
        except Exception as exc:
            print(f"[WARN] mihomo 精测失败: {exc}")

    # Fallback: 如果全部没通过，复用上一次输出
    if not metrics:
        metrics = load_existing_metrics()
        if metrics:
            print("[WARN] 无节点通过测试，复用上一次输出作为降级方案")

    if not metrics:
        print("[ERROR] 无可用节点，生成空订阅")
        _empty_output()
        return

    metrics.sort(key=lambda m: m.health_score, reverse=True)

    # 生成Clash配置（输出全部通过节点）
    config = generate_clash_config(metrics, meta_total=total_collected)
    CLASH_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with CLASH_OUTPUT.open("w", encoding="utf-8") as f:
        f.write(build_meta_header(total_collected=total_collected, kept=len(metrics), blocked=len(blocklist)))
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print(f"[OK] Clash配置已生成: {CLASH_OUTPUT} ({len(config.get('proxies', []))} 节点)")

    # 生成Shadowrocket + V2Ray订阅（输出全部通过节点，与原项目一致）
    # 注: 这两类订阅是纯 base64 节点列表, 不支持注释头; 元信息只在 Clash 配置的 YAML 头暴露。
    rocket_proxies = [m.proxy for m in metrics]
    rocket_content = generate_shadowrocket_sub(rocket_proxies)
    with ROCKET_OUTPUT.open("w", encoding="utf-8") as f:
        f.write(rocket_content)
    with V2RAY_OUTPUT.open("w", encoding="utf-8") as f:
        f.write(rocket_content)
    print(f"[OK] Shadowrocket/V2Ray订阅已生成: {len(rocket_proxies)} 节点")

    print(f"完成时间: {datetime.now(timezone.utc).isoformat()}")

    # 输出统计
    region_stats: dict[str, int] = {}
    for m in metrics:
        region_stats[m.region] = region_stats.get(m.region, 0) + 1
    print("\n=== 节点地区分布 ===")
    for region, count in sorted(region_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"{region}: {count}")
    avg_latency = round(sum(m.latency for m in metrics) / len(metrics)) if metrics else 0
    print(f"平均延迟: {avg_latency}ms")
    print(f"总节点: {len(metrics)}")


def load_existing_metrics() -> list[ProxyMetric]:
    """加载上一次输出的节点（降级方案）"""
    if not CLASH_OUTPUT.exists():
        return []
    try:
        data = yaml.safe_load(CLASH_OUTPUT.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("proxies"), list):
        return []
    metrics: list[ProxyMetric] = []
    for proxy in data["proxies"]:
        if not isinstance(proxy, dict):
            continue
        name = str(proxy.get("name", ""))
        region = detect_region({"name": name})
        metrics.append(
            ProxyMetric(
                proxy=dict(proxy),
                latency=LATENCY_TIMEOUT_MS,
                region=region,
                health_score=health_score(name, LATENCY_TIMEOUT_MS, region),
            )
        )
    return metrics


def _empty_output() -> None:
    """生成空输出文件"""
    CLASH_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    empty_config = {"proxies": [], "proxy-groups": [], "rules": ["MATCH,DIRECT"]}
    with CLASH_OUTPUT.open("w", encoding="utf-8") as f:
        yaml.safe_dump(empty_config, f)
    ROCKET_OUTPUT.write_text("", encoding="utf-8")
    V2RAY_OUTPUT.write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()