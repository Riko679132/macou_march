#!/usr/bin/env bash
# =====================================================================
# campus-paper-fetch：启动真人 Chrome（cdp 模式前置依赖）— macOS / Linux
#
# 用途：cdp 模式（browser_mode=cdp）下，工具附加到一个"真人启动"的
#       Chrome 实例上，避免 Playwright 启动的自动化 Chrome 触发
#       Cloudflare 指纹识别（反复人机验证、cf_clearance 不落地）。
#
# 幂等：端口 9222 已有 CDP 端点时直接提示并退出；重复执行安全。
#
# 自动降级：若 Chrome 首次启动即退出（常见于受限环境——沙箱/GPU 初始化
#       被拒，如容器、agent 沙箱），自动以
#       --no-sandbox --disable-gpu --disable-software-rasterizer
#       --disable-dev-shm-usage 重启，保证仍可用。
#
# 用法：
#   scripts/start-browser.sh            # 自动探测 Chrome 并启动
#   CPF_PORT=9223 scripts/start-browser.sh   # 自定义端口
#   CPF_CHROME=/path/to/chrome scripts/start-browser.sh  # 指定浏览器
#   CPF_EXTRA_FLAGS="--flag1 --flag2" scripts/start-browser.sh  # 追加参数
#   CPF_WAIT=40 scripts/start-browser.sh       # 调整就绪等待秒数（默认 25）
# Windows 用户请用 scripts/start-browser.ps1
# =====================================================================
set -u

PORT="${CPF_PORT:-9222}"
PROFILE_DIR="${CPF_PROFILE_DIR:-$HOME/.campus-paper-fetch/browser-profile}"
LOGFILE="${CPF_LOG:-/tmp/cpf-chrome.log}"
CDP_URL="http://127.0.0.1:${PORT}/json/version"
WAIT_SECS="${CPF_WAIT:-25}"

# 自动探测 Chrome 可执行文件（macOS / Linux 常见路径）
detect_chrome() {
  if [ -n "${CPF_CHROME:-}" ]; then
    echo "$CPF_CHROME"; return
  fi
  local candidates=(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    "/Applications/Chromium.app/Contents/MacOS/Chromium"
    "$(command -v google-chrome 2>/dev/null)"
    "$(command -v google-chrome-stable 2>/dev/null)"
    "$(command -v chromium 2>/dev/null)"
    "$(command -v chromium-browser 2>/dev/null)"
    "$(command -v microsoft-edge 2>/dev/null)"
  )
  for c in "${candidates[@]}"; do
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return; }
  done
  echo ""
}

# 等待 CDP 就绪，最多 $1 秒；就绪返回 0，否则返回 1
wait_cdp() {
  local n="$1"
  for _ in $(seq 1 "$n"); do
    if curl -sf --max-time 2 "$CDP_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# 启动 Chrome（$@ 为附加参数），清理残留锁文件，输出主进程 PID
launch_chrome() {
  rm -f "$PROFILE_DIR"/Singleton* 2>/dev/null
  # shellcheck disable=SC2086 —— CPF_EXTRA_FLAGS 有意按词拆分
  nohup "$CHROME_BIN" \
    --user-data-dir="$PROFILE_DIR" \
    --remote-debugging-port="$PORT" \
    --no-first-run --no-default-browser-check --no-proxy-server \
    ${CPF_EXTRA_FLAGS:-} "$@" \
    >"$LOGFILE" 2>&1 &
  echo $!
}

CHROME_BIN="$(detect_chrome)"

# 幂等检查：CDP 已就绪
if curl -sf --max-time 2 "$CDP_URL" >/dev/null 2>&1; then
  echo "Chrome 已在运行（CDP :${PORT}），无需重复启动。"
  exit 0
fi

if [ -z "$CHROME_BIN" ]; then
  echo "未找到 Chrome/Chromium。请安装 Google Chrome 后用 CPF_CHROME 指定路径：" >&2
  echo "  CPF_CHROME=/path/to/chrome scripts/start-browser.sh" >&2
  exit 1
fi

mkdir -p "$PROFILE_DIR"

# 第一次：常规参数启动
echo "启动 Chrome（profile: $PROFILE_DIR, CDP: :${PORT}）..."
PID="$(launch_chrome)"
if wait_cdp "$WAIT_SECS"; then
  echo "✓ Chrome 已就绪：ws://127.0.0.1:${PORT}（日志: ${LOGFILE}）"
  exit 0
fi

# 进程已退出 → 大概率是环境不允许沙箱/GPU 初始化，自动降级重启
if ! kill -0 "$PID" 2>/dev/null; then
  echo "Chrome 首次启动即退出（常见于受限环境：沙箱/GPU 初始化被拒），自动降级重启…" >&2
  PID="$(launch_chrome --no-sandbox --disable-gpu --disable-software-rasterizer --disable-dev-shm-usage)"
  if wait_cdp "$WAIT_SECS"; then
    echo "✓ Chrome 已就绪（降级模式 --no-sandbox --disable-gpu）：ws://127.0.0.1:${PORT}（日志: ${LOGFILE}）"
    exit 0
  fi
  echo "✗ 降级重启后仍超时，Chrome 未就绪。查看日志：${LOGFILE}" >&2
  exit 1
fi

echo "✗ 等待超时，Chrome 未就绪（进程仍在，可能有弹窗或初始化卡住）。查看日志：${LOGFILE}" >&2
exit 1
