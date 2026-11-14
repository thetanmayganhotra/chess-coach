#!/bin/zsh
# Installs a macOS launch agent that runs sync.py every day at 07:00 (or at next wake if the Mac was asleep).
# Remove with: launchctl bootout gui/$UID ~/Library/LaunchAgents/com.chesscoach.sync.plist && rm ~/Library/LaunchAgents/com.chesscoach.sync.plist
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST=~/Library/LaunchAgents/com.chesscoach.sync.plist
mkdir -p ~/Library/LaunchAgents
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.chesscoach.sync</string>
  <key>ProgramArguments</key><array><string>$DIR/.venv/bin/python</string><string>$DIR/sync.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$DIR/sync-daily.log</string>
  <key>StandardErrorPath</key><string>$DIR/sync-daily.log</string>
  <key>LowPriorityIO</key><true/><key>Nice</key><integer>10</integer>
</dict></plist>
PL
launchctl bootout gui/$UID "$PLIST" 2>/dev/null
launchctl bootstrap gui/$UID "$PLIST" && echo "Daily sync installed (07:00)."
