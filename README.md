# Free Grab Node

免费代理节点自动提取和聚合系统，同时生成 Clash 和 Shadowrocket（小火箭）订阅格式。GitHub Actions 每30分钟自动更新节点源。

## 核心机制

- 自动下载 **mihomo（Clash Meta）** 代理引擎进行真实代理延迟测试
- 通过代理发出 HTTP 请求验证连通性，无效节点直接丢弃
- 按健康评分排名，仅保留 **Top 15** 高质量节点
- 评分公式：`(1/延迟)×0.6 + 地区加成×0.3 + 稳定性×0.1`

## 功能特性

- 自动从多个公开源提取免费代理节点
- 节点去重和标准化处理
- 自动地区识别（香港、日本、美国、台湾、新加坡、韩国）
- 同时生成 Clash 和 Shadowrocket 两种订阅格式
- 支持全部主流代理协议（SS/SSR/VMess/VLESS/Trojan/Hysteria2/TUIC/HTTP/SOCKS5）
- 真实代理延迟测试，精准过滤无效节点
- GitHub Actions 每30分钟全自动更新


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

## 启用 GitHub Pages

1. 进入仓库 Settings → Pages
2. Source 选择 `/docs` 目录
3. 保存后等待部署

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
