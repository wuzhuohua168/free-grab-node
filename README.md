# Free Proxy Airport

免费代理节点自动提取和聚合系统，生成Clash配置文件。GitHub Actions 每30分钟自动更新节点源。

## 功能特性

- 自动从多个公开源提取免费代理节点
- 节点去重和标准化处理
- 自动地区识别（香港、日本、美国、台湾、新加坡、韩国）
- 智能分组（AUTO-FAST、HK-POOL、JP-POOL、US-POOL、AI-POOL）
- 自动生成Clash配置文件
- 支持AI服务智能分流（OpenAI、ChatGPT、Claude等）
- GitHub Actions自动更新

## 使用方法

### Clash Verge / Mihomo 订阅

在 Clash Verge 的 Profiles 中添加订阅URL：

```
https://your-username.github.io/free-proxy-airport/clash.yaml
```

将 `your-username` 替换为你的GitHub用户名。

### 手动运行

克隆仓库后，本地运行：

```bash
# 安装依赖
pip install requests pyyaml

# 运行脚本
python generator.py
```

生成的配置文件位于 `output/clash.yaml`。

## 节点源

本项目从以下公开源提取节点：

- openRunner/clash-freenode
- snakem982/proxypool
- Flikify/Free-Node
- aiboboxx/v2rayfree

## 项目结构

```
free-proxy-airport/
├── .github/
│   └── workflows/
│       └── update.yml      # GitHub Actions 配置
├── docs/
│   ├── clash.yaml          # GitHub Pages 订阅文件
│   └── .nojekyll           # 禁用Jekyll处理
├── output/
│   └── clash.yaml          # 生成的配置文件
├── generator.py            # 核心脚本
└── README.md               # 项目说明
```

## 自动更新

GitHub Actions 每30分钟自动运行一次，执行以下操作：

1. 从各个节点源获取最新节点
2. 去重和标准化处理
3. 测试节点延迟
4. 生成Clash配置文件
5. 提交并推送到GitHub仓库
6. 验证配置文件有效性

## 配置说明

生成的Clash配置包含以下功能：

### 代理组

- **PROXY**: 主代理选择组
- **AUTO-FAST**: 自动选择最快的节点
- **FALLBACK**: 自动降级组
- **HK-POOL**: 香港节点组
- **JP-POOL**: 日本节点组
- **US-POOL**: 美国节点组
- **AI-POOL**: AI服务专用节点组

### 分流规则

- AI服务（OpenAI、ChatGPT、Claude、Anthropic）→ AI-POOL
- Google服务 → PROXY
- GitHub → PROXY
- 国内网站（baidu、taobao、jd等）→ DIRECT
- GeoIP CN → DIRECT
- 其他 → PROXY

## 启用 GitHub Pages

要使订阅链接生效，需要启用 GitHub Pages：

1. 进入仓库 Settings → Pages
2. Source 选择 `gh-pages` 分支或 `/docs` 目录
3. 保存后等待部署完成

订阅地址将变为：
```
https://your-username.github.io/free-proxy-airport/clash.yaml
```

## 注意事项

⚠️ **重要提示**

- 本项目仅供学习和研究使用
- 免费节点可能不稳定，请勿用于重要业务
- 请遵守当地法律法规和网络安全规定
- 请勿用于非法用途

## 支持的代理类型

- Shadowsocks (ss)
- ShadowsocksR (ssr)
- VMess
- VLESS
- Trojan
- Hysteria / Hysteria2
- TUIC
- SOCKS5
- HTTP

## 许可证

MIT License

## 致谢

感谢所有公开节点源的提供者。