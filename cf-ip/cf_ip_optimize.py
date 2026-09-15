#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cf_ip_optimize.py —— 本地优选 Cloudflare IP 并推送到 GitHub 仓库

适用场景：
    家里有一台 24 小时在线的设备（NAS / 软路由 / 小主机），出口是你想优化的宽带
    （比如移动）。脚本从该设备本地发起 TCP 测速，选出延迟最低的 N 个 CF IP，
    推送到你的 GitHub 仓库（例如 wuzhuohua168/free-grab-node 的 cf-ip/best.txt），
    再可选通知 OpenClash / Nikki 刷新代理。edgetunnel 订阅这个 raw 文件即可。

为什么必须本地跑、不能放 GitHub Actions：
    GitHub 的 runner 不在你的宽带里，从它测出的“延迟”对你毫无意义。
    优选的本质是“从你家网络出口测量到各 CF 节点的延迟”，所以要在 NAS 本地执行。

仅依赖 Python 标准库，无需 pip install。

用法示例：
    # 基本：推送到 free-grab-node 仓库的 cf-ip/best.txt
    python3 cf_ip_optimize.py --token $GH_TOKEN --repo wuzhuohua168/free-grab-node

    # 用自定义 IP 列表（每行 ip 或 ip:port），优先于官方段
    python3 cf_ip_optimize.py --token $GH_TOKEN --input my_ips.txt

    # 只测速不推送，先看效果
    python3 cf_ip_optimize.py --dry-run

    # 测完顺便让 OpenClash 刷新订阅（mihomo external-controller 模式）
    python3 cf_ip_optimize.py --token $GH_TOKEN \
        --clash-mode mihomo --clash-api http://192.168.1.1:9090 \
        --clash-secret your_secret --clash-provider "edgetunnel订阅"

环境变量（与上面的 -- 参数同名，可二选一）：
    GH_TOKEN  GH_REPO  CLASH_MODE  CLASH_API  CLASH_SECRET  CLASH_PROVIDER
    CLASH_SSH  CLASH_SSH_CMD
"""

import argparse
import base64
import concurrent.futures as cf
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

CF_IPS_V4_URL = "https://www.cloudflare.com/ips-v4"
GITHUB_API = "https://api.github.com/repos"
BEIJING = timezone(timedelta(hours=8))

# CF 官方 IPv4 段兜底列表（拉取失败时启用，避免完全跑不起来）
CF_CIDRS_FALLBACK = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/16", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cf-ip-optimizer"


def build_opener():
    """带 UA 与代理支持的 opener（Cloudflare 会拦截默认 Python UA）。"""
    handlers = []
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        log("使用代理:", proxy)
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [("User-Agent", UA)]
    return opener


OPENER = build_opener()


def log(*a):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *a, flush=True)


# ---------- 1. 候选 IP ----------
def fetch_cf_cidrs(url):
    try:
        with OPENER.open(url, timeout=15) as r:
            txt = r.read().decode()
        cidrs = [l.strip() for l in txt.splitlines() if l.strip() and "/" in l]
        if cidrs:
            return cidrs
        log("拉取到的内容为空，改用内置兜底段")
    except Exception as e:
        log("拉取 CF 官方段失败:", e, "-> 改用内置兜底段")
    return list(CF_CIDRS_FALLBACK)


def build_candidates(cidrs, per_cidr=200, max_total=6000):
    cands = []
    for c in cidrs:
        try:
            net = ipaddress.ip_network(c, strict=False)
        except ValueError:
            continue
        if net.version != 4:
            continue
        size = net.num_addresses
        if size <= per_cidr:
            hosts = list(net.hosts())
        else:
            step = size // per_cidr
            hosts = [net.network_address + i * step for i in range(per_cidr)]
        for h in hosts:
            cands.append(str(h))
        if len(cands) >= max_total:
            break
    return cands[:max_total]


# ---------- 2. TCP 测速（TCPing 思路：测三次握手 RTT）----------
def tcp_ping(ip, port, timeout, samples=3):
    rtts = []
    for _ in range(samples):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                rtts.append((time.perf_counter() - t0) * 1000)
        except (OSError, socket.timeout):
            return None
    return sum(rtts) / len(rtts) if rtts else None


def scan(candidates, ports, timeout, workers):
    results = {}
    tasks = [(ip, p) for ip in candidates for p in ports]
    total = len(tasks)
    done = 0

    def work(task):
        ip, p = task
        avg = tcp_ping(ip, p, timeout)
        return ip, p, avg

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for ip, p, avg in ex.map(work, tasks):
            done += 1
            if done % 2000 == 0 or done == total:
                log(f"  进度 {done}/{total}，已有 {len(results)} 个有效 IP")
            if avg is None:
                continue
            cur = results.get(ip)
            if cur is None or avg < cur[0]:
                results[ip] = (avg, p)  # 记录该 IP 最优端口
    return results  # ip -> (avg_ms, best_port)


# ---------- 3. 选择 Top N ----------
def select(results, count, max_latency):
    items = [(ip, avg, p) for ip, (avg, p) in results.items() if avg <= max_latency]
    items.sort(key=lambda x: x[1])
    return items[:count]


# ---------- 4. 本地落盘 ----------
def write_local(top, outdir):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "best.txt")
    with open(path, "w", encoding="utf-8") as f:
        for ip, avg, p in top:
            f.write(f"{ip}:{p}\n")
    return path


# ---------- 5. 推送到 GitHub（Contents API，单文件）----------
def github_put(token, repo, path, branch, content, message):
    api = f"{GITHUB_API}/{repo}/contents/{urllib.parse.quote(path)}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    sha = None
    try:
        req = urllib.request.Request(f"{api}?ref={branch}", headers=headers)
        with OPENER.open(req, timeout=15) as r:
            sha = json.loads(r.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(
        api, data=json.dumps(body).encode(), headers=headers, method="PUT"
    )
    with OPENER.open(req, timeout=15) as r:
        return r.status


# ---------- 6. 通知 Clash 刷新 ----------
def clash_refresh(args):
    if args.clash_mode == "mihomo":
        if not args.clash_api or not args.clash_provider:
            log("mihomo 模式需要 --clash-api 和 --clash-provider，跳过刷新")
            return
        url = (
            f"{args.clash_api.rstrip('/')}/providers/proxies/"
            f"{urllib.parse.quote(args.clash_provider)}"
        )
        headers = {"Content-Type": "application/json"}
        if args.clash_secret:
            headers["Authorization"] = f"Bearer {args.clash_secret}"
        req = urllib.request.Request(
            url,
            data=json.dumps({"operation": "update"}).encode(),
            headers=headers,
            method="PUT",
        )
        try:
            with OPENER.open(req, timeout=10) as r:
                log("已通知 mihomo/OpenClash 刷新订阅:", r.status)
        except Exception as e:
            log("mihomo 刷新失败（可忽略，edgetunnel 会自行拉取新 IP）:", e)

    elif args.clash_mode == "ssh":
        if not args.clash_ssh:
            log("ssh 模式需要 --clash-ssh，跳过刷新")
            return
        cmd = ["ssh", args.clash_ssh, args.clash_ssh_cmd]
        try:
            subprocess.run(cmd, check=True)
            log("已通过 SSH 刷新路由器代理")
        except Exception as e:
            log("SSH 刷新失败:", e)


def main():
    ap = argparse.ArgumentParser(description="本地优选 CF IP 并推送到 GitHub")
    ap.add_argument("--repo", default=os.environ.get("GH_REPO", "wuzhuohua168/free-grab-node"))
    ap.add_argument("--token", default=os.environ.get("GH_TOKEN"))
    ap.add_argument("--branch", default="main")
    ap.add_argument("--path", default="cf-ip/best.txt")
    ap.add_argument("--count", type=int, default=20, help="保留延迟最低的 IP 数量")
    ap.add_argument("--ports", default="443,2053,8443,2087", help="待测端口，逗号分隔")
    ap.add_argument("--timeout", type=float, default=1.0, help="单 IP 握手超时(秒)")
    ap.add_argument("--max-latency", type=float, default=300.0, help="只保留低于该延迟(ms)的 IP")
    ap.add_argument("--per-cidr", type=int, default=200, help="每个 CF 段采样 IP 数")
    ap.add_argument("--max-total", type=int, default=6000, help="候选 IP 上限")
    ap.add_argument("--workers", type=int, default=200, help="并发线程数")
    ap.add_argument("--input", help="自定义 IP 列表文件(每行 ip 或 ip:port)，优先于官方段")
    ap.add_argument("--outdir", default="cf-ip-out")
    ap.add_argument("--dry-run", action="store_true", help="只测速不推送")
    ap.add_argument("--clash-mode", default=os.environ.get("CLASH_MODE", "off"),
                    choices=["off", "mihomo", "ssh"])
    ap.add_argument("--clash-api", default=os.environ.get("CLASH_API"))
    ap.add_argument("--clash-secret", default=os.environ.get("CLASH_SECRET"))
    ap.add_argument("--clash-provider", default=os.environ.get("CLASH_PROVIDER"))
    ap.add_argument("--clash-ssh", default=os.environ.get("CLASH_SSH"))
    ap.add_argument("--clash-ssh-cmd", default=os.environ.get("CLASH_SSH_CMD", "/etc/init.d/openclash restart"))
    args = ap.parse_args()

    # 候选
    if args.input:
        cands = []
        with open(args.input, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                cands.append(line.split(":")[0].split()[0])
        log(f"从文件载入 {len(cands)} 个候选 IP")
    else:
        cidrs = fetch_cf_cidrs(CF_IPS_V4_URL)
        log(f"获取到 {len(cidrs)} 个 CF 官方段")
        cands = build_candidates(cidrs, args.per_cidr, args.max_total)
        log(f"展开为 {len(cands)} 个候选 IP")

    ports = [int(x) for x in args.ports.split(",")]
    log(f"开始 TCP 测速（端口 {ports}，超时 {args.timeout}s，并发 {args.workers}）...")
    t0 = time.time()
    results = scan(cands, ports, args.timeout, args.workers)
    log(f"测速完成，有效 {len(results)} 个，耗时 {time.time() - t0:.1f}s")

    top = select(results, args.count, args.max_latency)
    if not top:
        log("没有满足延迟条件的 IP，退出")
        return

    log("Top 结果：")
    for ip, avg, p in top:
        log(f"  {ip}:{p}  {avg:.1f}ms")

    write_local(top, args.outdir)
    best_text = "".join(f"{ip}:{p}\n" for ip, avg, p in top)
    raw_url = f"https://raw.githubusercontent.com/{args.repo}/{args.branch}/{args.path}"
    log(f"本地已写：{os.path.join(args.outdir, 'best.txt')}")
    log(f"订阅地址：{raw_url}")

    if args.dry_run:
        log("dry-run 模式，已跳过推送与刷新")
        return

    if not args.token:
        log("未提供 --token / GH_TOKEN，无法推送（本地 best.txt 已生成）")
        return

    msg = f"优选IP自动更新 {datetime.now(BEIJING).strftime('%Y-%m-%d %H:%M')}"
    try:
        st = github_put(args.token, args.repo, args.path, args.branch, best_text, msg)
        log(f"已推送到 GitHub（{args.repo}/{args.path}）：HTTP {st}")
    except Exception as e:
        log("推送失败：", e)
        return

    if args.clash_mode != "off":
        clash_refresh(args)


if __name__ == "__main__":
    main()
