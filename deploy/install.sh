#!/bin/bash
# deploy/install.sh - 电信/移动云主机节点连通性测试环境一键部署
# 用法: CARRIER=telecom bash install.sh    # 电信线路
#       CARRIER=mobile  bash install.sh    # 移动线路
set -e

CARRIER="${CARRIER:-telecom}"
REPO="https://github.com/wuzhuohua168/free-grab-node.git"
GIT_USER="${GIT_USER:-github-actions[bot]}"
GIT_EMAIL="${GIT_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"

echo "=== 安装 mihomo 代理引擎 ==="
MIHOMO_VERSION="v1.18.0"
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
    MIHOMO_ARCH="amd64"
elif [ "$ARCH" = "aarch64" ]; then
    MIHOMO_ARCH="arm64"
else
    echo "不支持的架构: $ARCH"
    exit 1
fi

MIHOMO_URL="https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-${MIHOMO_ARCH}-${MIHOMO_VERSION}.gz"
wget -q "$MIHOMO_URL" -O /tmp/mihomo.gz
gunzip -f /tmp/mihomo.gz
mv /tmp/mihomo /usr/local/bin/mihomo
chmod +x /usr/local/bin/mihomo
echo "[OK] mihomo 已安装: $(mihomo -v 2>&1 | head -1)"

echo "=== 安装 Python 依赖 ==="
pip3 install requests pyyaml

echo "=== 克隆项目仓库 ==="
WORKDIR="/opt/free-grab-node"
if [ -d "$WORKDIR" ]; then
    cd "$WORKDIR" && git pull
else
    git clone "$REPO" "$WORKDIR"
fi
cd "$WORKDIR"

echo "=== 配置 git ==="
git config user.name "$GIT_USER"
git config user.email "$GIT_EMAIL"

echo "=== 配置运营商标识: $CARRIER ==="
echo "$CARRIER" > "$WORKDIR/deploy/carrier.txt"

echo "=== 设置 cron 定时任务 (每30分钟) ==="
(crontab -l 2>/dev/null | grep -v "free-grab-node"; echo "*/30 * * * * cd $WORKDIR && bash deploy/cron_job.sh >> /var/log/free-grab-node.log 2>&1") | crontab -

echo "=== 首次运行测试 ==="
bash "$WORKDIR/deploy/cron_job.sh"

echo "=== 部署完成 ==="
echo "运营商: $CARRIER"
echo "日志: tail -f /var/log/free-grab-node.log"
echo "cron: crontab -l"