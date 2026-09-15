#!/bin/bash
# 安装机票监控：建 venv、装 Playwright、注册 launchd 定时任务、装 flightmon 命令。
# 在监控目录里执行（该目录须已有 config.py）。
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.$(id -un).flightmonitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
INTERVAL="${INTERVAL:-1800}"

cd "$DIR"

if [ ! -f config.py ]; then
  echo "缺少 config.py —— 先从 config_template.py 复制一份并按航线改好。" >&2
  exit 1
fi

echo "==> 建立虚拟环境"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q playwright

echo "==> 安装 Chromium（约 95MB，首次较慢）"
./.venv/bin/playwright install chromium 2>&1 | tail -2

echo "==> 校验配置"
./.venv/bin/python -c "
import monitor, config
pairs = monitor.date_pairs()
assert pairs, '日期配置没有产生任何组合，检查 TRIP_NIGHTS 和日期设置'
print(f'  {config.ORIGIN} → {config.DEST}，{len(pairs)} 个日期组合，'
      f'目标 {config.money(config.TARGET_PRICE)}')
for o, r in pairs[:6]:
    print(f'    {o} → {r}')
print(f'    ...共 {len(pairs)} 组') if len(pairs) > 6 else None
"

echo "==> 注册 launchd 定时任务（每 ${INTERVAL} 秒）"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key><array><string>$DIR/run.sh</string></array>
    <key>StartInterval</key><integer>$INTERVAL</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$DIR/launchd.out.log</string>
    <key>StandardErrorPath</key><string>$DIR/launchd.err.log</string>
    <key>WorkingDirectory</key><string>$DIR</string>
</dict>
</plist>
PLISTEOF

chmod +x run.sh flightmon
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "==> 安装 flightmon 控制命令"
mkdir -p "$HOME/bin"
ln -sf "$DIR/flightmon" "$HOME/bin/flightmon"
case ":$PATH:" in
  *":$HOME/bin:"*) ;;
  *) printf '\nexport PATH="$HOME/bin:$PATH"\n' >> "$HOME/.zshrc"
     echo "  已把 ~/bin 加进 ~/.zshrc，新开终端生效" ;;
esac

cat <<DONE

安装完成。

  flightmon          看状态和最近结果
  flightmon now      立刻跑一轮（先用这个确认能抓到数据）
  flightmon stop     暂停      flightmon start  恢复
  flightmon log      实时日志  flightmon off    彻底关闭

DONE
