# Free Grab Node

免费代理节点自动提取和聚合系统，同时生成 Clash、Shadowrocket 和 V2Ray 订阅格式。GitHub Actions 每30分钟自动更新。

## 核心机制

- **双层测试**：TCP 快速粗筛 + mihomo 真实代理引擎精测
- 通过代理发出 HTTP 请求验证连通性，无效节点直接丢弃
- 支持**中国电信/移动线路**真实连通性测试（可选部署）
- 按延迟排名，保留 **Top 30** 高质量节点

## 中国线路测试（可选）

在广东电信/移动云服务器上部署测试节点，从中国 IP 验证代理真实可用性：

```bash
# 电信云服务器
curl -sL https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/deploy/install.sh | CARRIER=telecom bash

# 移动云服务器
curl -sL https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/deploy/install.sh | CARRIER=mobile bash
```

部署后，两台服务器每30分钟测试一次，结果自动推送回仓库，主流程合并中国线路测试结果，优先选择双向可通的节点。

## 功能特性

- 自动从多个公开源提取免费代理节点
- 节点去重和标准化处理
- 自动地区识别（香港、日本、美国、台湾、新加坡、韩国）
- 同时生成 Clash、Shadowrocket、V2Ray 三种订阅格式
- 支持全部主流代理协议（SS/SSR/VMess/VLESS/Trojan/Hysteria2/TUIC/HTTP/SOCKS5）
- 真实代理延迟测试，精准过滤无效节点
- GitHub Actions 每30分钟全自动更新

## 订阅链接

### Clash Verge / Mihomo

```
https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/docs/clash.yaml
```

### Shadowrocket / 小火箭

```
https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/docs/rocket.txt
```

### V2Ray / v2rayN

```
https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/docs/v2ray.txt
```

在客户端中新建订阅，类型选择对应的格式，填入链接即可。

## 项目结构

```
free-grab-node/
├── .github/
│   └── workflows/
│       └── update.yml      # GitHub Actions 配置
├── deploy/
│   ├── install.sh          # 云服务器一键部署脚本
│   ├── cron_job.sh         # 定时任务脚本
│   ├── tester.py           # 中国线路测试脚本
│   └── results.json        # 中国线路测试结果
├── docs/
│   ├── clash.yaml
│   ├── rocket.txt
│   ├── v2ray.txt
│   └── .nojekyll
├── output/
│   ├── clash.yaml
│   ├── rocket.txt
│   └── v2ray.txt
├── generator.py            # 核心脚本
└── README.md
```

## 自动更新

GitHub Actions 每30分钟自动运行，流程：

1. 从各节点源获取最新节点
2. 去重 → TCP 快速粗筛 → mihomo 真实代理精测
3. 合并中国线路测试结果（如有）
4. 按延迟排名取 Top 30
5. 生成 Clash + Shadowrocket + V2Ray 三种订阅
6. 自动推送回仓库
