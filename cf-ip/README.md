# cf-ip: 本地优选 Cloudflare IP

从你自己的网络出口测量 Cloudflare 节点延迟，自动选出最优 IP 并推送到仓库，供 edgetunnel 的 `PROXYIP` 订阅使用。

## 原理

```
你的宽带网络
    │
    ├─ TCPing 扫描 ~6000 个 CF IPv4（443/2053/8443/2087）
    │
    ├─ 选出延迟最低的 20 个 → cf-ip/best.txt
    │
    └─ 推送到本仓库 GitHub
          │
          └─ edgetunnel 通过 PROXYIP 订阅拉取 → 客户端使用最优 IP
```

**为什么不能放 GitHub Actions？** 优选的延迟数据只对发起测量的网络有意义。Actions 的 runner 不在你的宽带里，测出来的结果对你没用。必须从你家的 NAS / 路由器 / 小主机本地执行。

## 快速开始

### 前置条件

- Python 3.6+（仅用标准库，无需 pip install）
- [GitHub Personal Access Token](https://github.com/settings/tokens)（需要 `repo` 权限）
- 本仓库的写入权限

### 步骤

```bash
# 1. 下载脚本到 NAS / 路由器 / 小主机
curl -LO https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/cf-ip/cf_ip_optimize.py

# 2. 设置 GitHub Token
export GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# 3. 先 dry-run 测试（不推送）
python3 cf_ip_optimize.py --dry-run

# 4. 正式运行（推送到仓库）
python3 cf_ip_optimize.py --token $GH_TOKEN
```

运行后，文件 `cf-ip/best.txt` 会自动更新，edgetunnel 拉取地址：

```
https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/cf-ip/best.txt
```

## 定时运行

建议每 6-12 小时运行一次（IP 质量不会每分钟变化）：

```bash
# crontab 示例：每 6 小时执行
0 */6 * * * GH_TOKEN=ghp_xxx python3 /path/to/cf_ip_optimize.py >> /var/log/cf-optimize.log 2>&1
```

## edgetunnel 配置

在 edgetunnel 的环境变量中设置：

```
PROXYIP = https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/cf-ip/best.txt
```

或者结合 `BEST_SUB` 使用，edgetunnel 会自动从订阅地址拉取并轮换 IP。

## 高级用法

### 自定义 IP 列表

如果你有自己的 CF IP 列表（比如从其他工具导出的）：

```bash
# 文件格式：每行一个 ip 或 ip:port
python3 cf_ip_optimize.py --input my_ips.txt --token $GH_TOKEN
```

### 调整参数

```bash
python3 cf_ip_optimize.py \
    --count 30 \\            # 保留 Top 30 个 IP（默认 20）
    --max-latency 200 \\     # 只保留延迟 < 200ms 的（默认 300）
    --ports 443,2053 \\      # 只测这两个端口（默认 4 个都测）
    --workers 100 \\         # 并发线程数（默认 200，NAS 性能低可调小）
    --timeout 2 \\           # 握手超时 2 秒（默认 1）
    --token $GH_TOKEN
```

### mihomo / OpenClash 自动刷新（可选）

如果你有 OpenWrt 路由器跑 mihomo / OpenClash，可以让脚本测完自动通知刷新订阅：

```bash
python3 cf_ip_optimize.py --token $GH_TOKEN \
    --clash-mode mihomo \
    --clash-api http://192.168.1.1:9090 \
    --clash-secret your_secret \
    --clash-provider "edgetunnel订阅"
```

没有路由器也能正常工作——edgetunnel 本身会定期从订阅地址拉取新 IP，无需额外通知。

## best.txt 格式

每行一个 `IP:PORT`，示例：

```
104.16.132.229:443
104.17.32.158:2053
104.18.164.250:8443
```

## 与 generator.py 的关系

`generator.py` 管理代理节点抓取与订阅生成，运行在 GitHub Actions 上，每 30 分钟更新 `output/` 目录。

`cf_ip_optimize.py` 管理 CF 优选 IP，运行在你的本地网络，独立更新 `cf-ip/` 目录。

两者互不干扰，各自负责各自的功能。
