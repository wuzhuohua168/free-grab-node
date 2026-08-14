# Free Grab Node

免费代理节点自动提取和聚合系统，同时生成 Clash 和 Shadowrocket（小火箭）订阅格式。GitHub Actions 每30分钟自动更新节点源。

## 功能特性

- 自动从多个公开源提取免费代理节点
- 节点去重和标准化处理
- 自动地区识别（香港、日本、美国、台湾、新加坡、韩国）
- 同时生成 Clash 和 Shadowrocket 两种订阅格式
- 支持全部主流代理协议（SS/SSR/VMess/VLESS/Trojan/Hysteria2/TUIC/HTTP/SOCKS5）
- AI服务智能分流
- GitHub Actions 全自动更新

## 订阅链接

### Clash Verge / Mihomo

```
https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/docs/clash.yaml
```

### Shadowrocket / 小火箭

```
https://raw.githubusercontent.com/wuzhuohua168/free-grab-node/main/docs/rocket.txt
```

在 Shadowrocket 中点击右上角 + → 类型选择 **Subscribe** → URL 填入上面的链接。

## 使用方法

### 手动运行

克隆仓库后，本地运行：

```bash
# 安装依赖
pip install requests pyyaml

# 运行脚本
python generator.py
```

生成的文件：
- `output/clash.yaml` - Clash 配置
- `output/rocket.txt` - Shadowrocket 订阅（base64编码）

## 节点源

本项目从以下公开源提取节点：

- openRunner/clash-freenode
- snakem982/proxypool
- Flikify/Free-Node
- aiboboxx/v2rayfree

## 项目结构

```
free-grab-node/
├── .github/
│   └── workflows/
│       └── update.yml      # GitHub Actions 配置
├── docs/
│   ├── clash.yaml          # Clash 订阅
│   ├── rocket.txt          # Shadowrocket 订阅
│   └── .nojekyll
├── output/
│   ├── clash.yaml
│   └── rocket.txt
├── generator.py            # 核心脚本
└── README.md
```

## 自动更新

GitHub Actions 每30分钟自动运行一次，执行以下操作：

1. 从各个节点源获取最新节点
2. 去重和标准化处理
3. 测试节点延迟
4. 生成 Clash + Shadowrocket 双格式订阅
5. 提交并推送到 GitHub Pages

## 配置说明

### Clash 代理组

- **PROXY**: 主代理选择组
- **AUTO-FAST**: 自动选择最快节点
- **FALLBACK**: 自动降级
- **HK-POOL / JP-POOL / US-POOL**: 地区分组
- **AI-POOL**: AI服务专用

### 分流规则

- AI服务（OpenAI、ChatGPT、Claude等）→ AI-POOL
- Google / GitHub → PROXY
- 国内网站 → DIRECT
- GeoIP CN → DIRECT

## 启用 GitHub Pages（可选）

如需使用更短域名 `github.io` 访问，可启用 GitHub Pages：

1. 进入仓库 Settings → Pages
2. Source 选择 Deploy from a branch
3. Branch 选择 `main`，文件夹选择 `/docs`
4. 保存并等待部署完成

启用后订阅地址：
```
https://wuzhuohua168.github.io/free-grab-node/clash.yaml
https://wuzhuohua168.github.io/free-grab-node/rocket.txt
```

## 支持的代理类型

| 协议 | Clash | Shadowrocket |
|------|-------|-------------|
| Shadowsocks (ss) | ✅ | ✅ |
| ShadowsocksR (ssr) | ✅ | ✅ |
| VMess | ✅ | ✅ |
| VLESS | ✅ | ✅ |
| Trojan | ✅ | ✅ |
| Hysteria / Hysteria2 | ✅ | ✅ |
| TUIC | ✅ | ✅ |
| SOCKS5 | ✅ | ✅ |
| HTTP | ✅ | ✅ |

## 注意事项

- 本项目仅供学习和研究使用
- 免费节点可能不稳定，请勿用于重要业务
- 请遵守当地法律法规和网络安全规定
- 请勿用于非法用途

## 许可证

MIT License