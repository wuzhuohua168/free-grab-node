#!/bin/bash
# deploy/cron_job.sh - cron 定时任务脚本
# 拉取最新节点配置，运行 mihomo 测试，推送结果
set -e

WORKDIR="/opt/free-grab-node"
cd "$WORKDIR"

echo "[$(date)] 开始电信/移动线路连通性测试"

# 拉取最新代码和节点配置
git pull origin main

# 读取运营商标识
CARRIER=$(cat deploy/carrier.txt 2>/dev/null || echo "unknown")
echo "运营商: $CARRIER"

# 复制最新的 clash 配置作为测试输入
cp docs/clash.yaml /tmp/benchmark.yaml

# 启动 mihomo
mihomo -d /tmp/mihomo-test -f /tmp/benchmark.yaml &
MIHOMO_PID=$!
sleep 3

# 运行测试
python3 deploy/tester.py --carrier "$CARRIER" --output deploy/results.json

# 停止 mihomo
kill $MIHOMO_PID 2>/dev/null || true

# 推送结果回仓库
git add deploy/results.json
if git diff --cached --quiet; then
    echo "[$(date)] 结果无变化，跳过推送"
else
    git commit -m "update $CARRIER test results - $(date '+%Y-%m-%d %H:%M:%S')"
    git push origin main
    echo "[$(date)] 已推送测试结果 ($CARRIER)"
fi