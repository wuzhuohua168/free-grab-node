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

VERSION = "v1.2.1"
CLASH_OUTPUT = Path("output/clash.yaml")
ROCKET_OUTPUT = Path("output/rocket.txt")
V2RAY_OUTPUT = Path("output/v2ray.txt")
TEST_URL = "http://www.gstatic.com/generate_204"
SOURCE_TIMEOUT = 25
LATENCY_TIMEOUT_MS = 5000
MAX_RETRIES = 3
MAX_WORKERS = int(os.getenv("FREE_PROXY_MAX_WORKERS", "24"))
MAX_CANDIDATES = int(os.getenv("FREE_PROXY_MAX_CANDIDATES", "500"))  # 限制mihomo精测节点数，过多会崩溃

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
        "name": "dongchengjie airport",
        "primary": "https://raw.githubusercontent.com/dongchengjie/airport/refs/heads/main/subs/merged/tested_within.yaml",
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


# 代理可用性测试URL（与参考项目一致，单URL测试）
PROXY_TEST_URLS = [
    "http://www.gstatic.com/generate_204",
]
# 有效地区列表
VALID_REGIONS = {"HK", "JP", "SG", "US", "KR", "TW"}
# 保留节点数（0 = 不限制，输出全部通过节点）
TOP_N = 15
# check-host.net 中国节点验证（暂时禁用，先验证基线可用后再启用）
CHINA_CHECK_ENABLED = False
CHINA_CHECK_MAX_NODES = 50


def health_score(name: str, latency: int, region: str) -> float:
    """计算节点健康评分（与参考项目一致：延迟权重60% + 地区权重30% + 稳定性10%）"""
    if region in {"HK", "SG", "JP"}:
        region_bonus = 3
    elif region == "US":
        region_bonus = 2
    else:
        region_bonus = 1
    stability_seed = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], 16)
    stability = random.Random(stability_seed).random()
    return (1.0 / max(latency, 1)) * 0.6 + region_bonus * 0.3 + stability * 0.1


def merge_china_results(metrics: list[ProxyMetric]) -> list[ProxyMetric]:
    """合并中国电信/移动线路测试结果，提升双向可通节点的评分"""
    results_path = Path("deploy/results.json")
    if not results_path.exists():
        print("[INFO] 无中国线路测试结果，跳过合并")
        return metrics

    try:
        with results_path.open("r", encoding="utf-8") as f:
            china_data = json.load(f)
    except Exception as e:
        print(f"[WARN] 读取中国线路结果失败: {e}")
        return metrics

    results = china_data.get("results", {})
    if not results:
        print("[INFO] 中国线路测试结果为空")
        return metrics

    # 建立 name -> passed 映射
    china_passed: set[str] = set()
    for name, vals in results.items():
        if vals.get("connectivity") or vals.get("google"):
            china_passed.add(name)

    print(f"[INFO] 中国线路通过节点: {len(china_passed)}")

    if not china_passed:
        return metrics

    # 对通过中国线路测试的节点提升评分
    for m in metrics:
        if m.proxy["name"] in china_passed:
            m.health_score *= 2.0  # 通过中国线路的节点评分翻倍

    return metrics


# ---- Mihomo 代理引擎测试 ----

def _tcp_quick_test(proxy: dict[str, Any]) -> tuple[dict[str, Any], int] | None:
    """快速TCP连通性测试（3秒超时，用于粗筛）"""
    server = str(proxy.get("server", ""))
    port = proxy.get("port", 0)
    if not server or not port:
        return None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        start = time.time()
        sock.connect((server, port))
        latency = int((time.time() - start) * 1000)
        sock.close()
        if latency == 0 or latency > 800:  # CDN或超慢节点
            return None
        return (proxy, latency)
    except Exception:
        return None


def tcp_prescreen(proxies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """TCP快速粗筛，大幅减少需要 mihomo 精测的节点数"""
    if len(proxies) <= 300:
        return proxies

    print(f"[INFO] TCP快速粗筛 {len(proxies)} 个节点...")
    passed: list[dict[str, Any]] = []
    workers = max(1, min(MAX_WORKERS, len(proxies)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_tcp_quick_test, p): p for p in proxies}
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result:
                passed.append(result[0])
            if completed % 200 == 0 or completed == len(futures):
                print(f"[INFO] TCP粗筛: {completed}/{len(futures)} passed={len(passed)}")

    print(f"[INFO] TCP粗筛完成: {len(passed)}/{len(proxies)} 进入mihomo精测")
    return passed


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


def _test_single_proxy(controller_url: str, proxy: dict[str, Any]) -> ProxyMetric | None:
    """通过 mihomo 引擎测试单个代理节点（单URL测试，与参考项目一致）"""
    name = str(proxy["name"])
    url = (
        f"{controller_url}/proxies/{quote(name, safe='')}/delay"
        f"?timeout={LATENCY_TIMEOUT_MS}&url={quote(TEST_URL, safe='')}"
    )
    response = requests.get(url, timeout=(LATENCY_TIMEOUT_MS / 1000) + 3)
    if response.status_code != 200:
        return None
    data = response.json()
    latency = int(data.get("delay", 0))
    if latency <= 0 or latency > LATENCY_TIMEOUT_MS:
        return None
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


# ---- check-host.net 中国节点 TCP 验证（免费，无需服务器） ----

# check-host.net 中国节点TCP验证
CHINA_CHECK_REGIONS = {"cn", "hk", "tw", "sg", "jp", "kr"}  # 亚太节点（GFW上游，接近中国网络环境）


def _check_host_china_tcp(server: str, port: int) -> tuple[bool, int]:
    """通过 check-host.net 免费API验证中国/亚太节点TCP连通性
    返回 (是否通过, 通过的亚太节点数)
    """
    if not CHINA_CHECK_ENABLED:
        return True, 0

    try:
        # 发起检测请求，max_nodes=10 增加命中中国节点的概率
        init_resp = requests.get(
            "https://check-host.net/check-tcp",
            params={"host": f"{server}:{port}", "max_nodes": 10},
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if init_resp.status_code != 200:
            return False, 0
        data = init_resp.json()
        request_id = data.get("request_id")
        if not request_id:
            return False, 0

        # 从 init 响应中读取节点国家代码（hostname 不含国家信息）
        # 格式: {"sg1.node.check-host.net": ["sg", "Singapore", ...], ...}
        nodes_info = data.get("nodes", {})

        # 等待结果
        time.sleep(4)

        # 获取结果
        result_resp = requests.get(
            f"https://check-host.net/check-result/{request_id}",
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if result_resp.status_code != 200:
            return False, 0

        results = result_resp.json()
        china_passed = 0

        for node_host, node_result in results.items():
            if not isinstance(node_result, list) or not node_result:
                continue
            # 用 init 响应中的国家代码判断是否为中国/亚太节点
            node_meta = nodes_info.get(node_host)
            if not node_meta or not isinstance(node_meta, list) or len(node_meta) == 0:
                continue
            country_code = str(node_meta[0]).lower()
            if country_code not in CHINA_CHECK_REGIONS:
                continue
            # 检查TCP连接是否成功（成功: {"address": "x.x.x.x", "time": 0.004}）
            for r in node_result:
                if isinstance(r, dict) and r.get("error") is None:
                    china_passed += 1
                    break

        passed = china_passed >= 1  # 至少1个亚太节点通过
        return passed, china_passed

    except Exception as e:
        print(f"[WARN] check-host.net 验证失败 ({server}:{port}): {e}")
        return False, 0


def china_tcp_filter(metrics: list[ProxyMetric]) -> list[ProxyMetric]:
    """通过 check-host.net 中国/亚太节点TCP验证过滤节点（硬过滤）"""
    if not CHINA_CHECK_ENABLED:
        return metrics

    top_n = min(CHINA_CHECK_MAX_NODES, len(metrics))
    if top_n == 0:
        return metrics

    print(f"[INFO] check-host.net 中国TCP验证: 对Top {top_n} 节点进行验证...")
    candidates = metrics[:top_n]

    verified: list[ProxyMetric] = []
    failed = 0

    for i, m in enumerate(candidates):
        server = str(m.proxy.get("server", ""))
        port = m.proxy.get("port", 0)
        if not server or not port:
            continue

        passed, china_nodes = _check_host_china_tcp(server, port)
        if passed:
            # 通过中国验证的节点大幅加分
            m.health_score *= 1.5
            verified.append(m)
            if (i + 1) % 10 == 0 or i == len(candidates) - 1:
                print(f"[INFO] 中国TCP: {i + 1}/{len(candidates)} verified={len(verified)}")
        else:
            failed += 1
        # 控制请求频率，避免被限流
        time.sleep(0.8)

    print(f"[INFO] 中国TCP验证完成: {len(verified)} 通过, {failed} 未通过")

    # 硬过滤：只保留通过中国验证的节点，不凑数
    # 未进入Top N的节点保留（它们没被验证过，作为后备）
    rest = metrics[top_n:]

    if verified:
        verified.sort(key=lambda m: m.health_score, reverse=True)
        result = verified + rest
        print(f"[INFO] 中国TCP硬过滤: 验证通过 {len(verified)} + 未验证后备 {len(rest)} = 共 {len(result)} 节点")
        return result
    else:
        # 全部未通过验证，返回原结果（但标记警告）
        print("[WARN] 中国TCP验证全部未通过，保留原mihomo结果（节点质量可能较差）")
        return metrics


# ---- 节点稳定性追踪（跨轮次存活加分） ----

HISTORY_FILE = Path("output/node_history.json")


def load_node_history() -> dict[str, int]:
    """加载历史节点记录，返回 {fingerprint: 存活轮次}"""
    if not HISTORY_FILE.exists():
        return {}
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("nodes", {})
    except Exception:
        return {}


def save_node_history(metrics: list[ProxyMetric]) -> None:
    """保存当前轮次节点记录"""
    history = load_node_history()
    new_history: dict[str, int] = {}

    for m in metrics[:TOP_N]:
        fp = proxy_fingerprint(m.proxy)
        prev_rounds = history.get(fp, 0)
        new_history[fp] = min(prev_rounds + 1, 3)  # 最多记录3轮

    # 历史节点衰减：之前出现过的节点如果本轮不在TopN，轮次-1
    current_fps = {proxy_fingerprint(m.proxy) for m in metrics[:TOP_N]}
    for fp, rounds in history.items():
        if fp not in current_fps and rounds > 1:
            new_history[fp] = rounds - 1

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("w", encoding="utf-8") as f:
        json.dump({"nodes": new_history, "updated": datetime.now(timezone.utc).isoformat()}, f, ensure_ascii=False, indent=2)


def apply_stability_bonus(metrics: list[ProxyMetric]) -> list[ProxyMetric]:
    """为跨轮次稳定存活的节点加分"""
    history = load_node_history()
    if not history:
        print("[INFO] 无历史节点记录，跳过稳定性加分")
        return metrics

    stable_count = 0
    for m in metrics:
        fp = proxy_fingerprint(m.proxy)
        rounds = history.get(fp, 0)
        if rounds >= 2:
            # 存活2轮 +20%，3轮 +30%
            bonus = 1.0 + rounds * 0.1
            m.health_score *= bonus
            stable_count += 1

    print(f"[INFO] 稳定性加分: {stable_count} 个历史存活节点获得加分")
    metrics.sort(key=lambda m: m.health_score, reverse=True)
    return metrics


def generate_clash_config(metrics: list[ProxyMetric]) -> dict[str, Any]:
    """生成Clash配置文件"""
    metrics.sort(key=lambda m: m.health_score, reverse=True)
    valid_metrics = metrics[:TOP_N] if TOP_N > 0 else metrics

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
    """主函数（简化版，对齐参考项目基线）"""
    print(f"=== Free Proxy Grab Node {VERSION} ===")
    print(f"开始时间: {datetime.now(timezone.utc).isoformat()}")

    # 收集代理节点
    total_collected, proxies = collect_proxies()
    print(f"[OK] 收集到 {total_collected} 个节点，去重后 {len(proxies)} 个")

    # 限制mihomo精测节点数（过多会导致mihomo进程崩溃）
    if MAX_CANDIDATES > 0 and len(proxies) > MAX_CANDIDATES:
        proxies = proxies[:MAX_CANDIDATES]
        print(f"[INFO] 节点过多，限制为 {MAX_CANDIDATES} 个进入mihomo精测")

    # mihomo 真实代理延迟测试
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

    # 合并中国线路测试结果（如有部署云服务器）
    metrics = merge_china_results(metrics)

    metrics.sort(key=lambda m: m.health_score, reverse=True)

    # 生成Clash配置
    config = generate_clash_config(metrics)
    CLASH_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with CLASH_OUTPUT.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print(f"[OK] Clash配置已生成: {CLASH_OUTPUT} ({len(config.get('proxies', []))} 节点)")

    # 生成Shadowrocket + V2Ray订阅
    rocket_proxies = [m.proxy for m in metrics[:TOP_N]] if TOP_N > 0 else [m.proxy for m in metrics]
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