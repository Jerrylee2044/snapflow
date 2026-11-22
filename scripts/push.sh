#!/usr/bin/env bash
# 本机 git 全局配置了 ghfast.top 下载加速（insteadOf 重写），
# 但镜像不支持推送认证。推送前临时摘掉重写，结束后恢复。
set -euo pipefail

cd "$(dirname "$0")/.."

RULE='url.https://ghfast.top/https://github.com/.insteadOf'

restore() {
    git config --global "$RULE" https://github.com/
}
trap restore EXIT

git config --global --unset "$RULE" 2>/dev/null || true
git push origin main
