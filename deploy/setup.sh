#!/usr/bin/env bash
# One-time (and re-runnable) server setup for the hidden-op collector.
#
#   cd ~/hidden-op && bash deploy/setup.sh
#
# Works on Ubuntu 22.04/24.04, ARM (Oracle Ampere) or x86 (Google e2-micro).
# It asks for anything it needs: your Riot API key, and a Google Drive token for rclone.
# Re-run it any time after `git pull` to pick up changes; it keeps your key and Drive login.
#
# Optional settings (environment variables):
#   PLATFORMS="na1 euw1 kr"     regions to collect
#   HIDDENOP_REMOTE=gdrive:hidden-op   Drive folder for archives and snapshots
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
PLATFORMS="${PLATFORMS:-na1 euw1 kr}"
cd "$DIR"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

if [[ $EUID -eq 0 ]]; then
  echo "Run this as your normal user (e.g. ubuntu), not root. It uses sudo where needed." >&2
  exit 1
fi

step "Installing system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv sqlite3 curl unzip >/dev/null
sudo timedatectl set-timezone UTC || true

if ! command -v rclone >/dev/null; then
  step "Installing rclone"
  curl -fsSL https://rclone.org/install.sh | sudo bash >/dev/null
fi
rclone version | head -1

# Small VMs (Google e2-micro has 1 GB RAM) get a swap file so pip and SQLite don't run out of memory.
if (( $(awk '/MemTotal/ {print $2}' /proc/meminfo) < 2000000 )) && ! swapon --show | grep -q .; then
  step "Adding a 2 GB swap file (small VM)"
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

step "Python environment"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q requests   # the collector only needs requests; the model runs elsewhere

step "Riot API key"
touch .env && chmod 600 .env
if grep -q '^RIOT_API_KEY=RGAPI-' .env && ! grep -q 'xxxxxxxx' .env; then
  echo "Already in .env."
else
  read -rp "Paste your Riot API key (RGAPI-...): " KEY
  KEY="$(echo "$KEY" | tr -d '[:space:]')"
  [[ "$KEY" == RGAPI-* ]] || { echo "That doesn't look like a Riot key." >&2; exit 1; }
  sed -i '/^RIOT_API_KEY=/d' .env
  echo "RIOT_API_KEY=$KEY" >> .env
fi
code=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Riot-Token: $(grep '^RIOT_API_KEY=' .env | cut -d= -f2-)" \
  https://na1.api.riotgames.com/lol/status/v4/platform-data)
if [[ "$code" == 200 ]]; then echo "Key works."; else
  echo "Riot answered HTTP $code for this key. Fix RIOT_API_KEY in $DIR/.env and re-run." >&2; exit 1; fi

REMOTE="${HIDDENOP_REMOTE:-$(grep -s '^HIDDENOP_REMOTE=' .env | cut -d= -f2-)}"
REMOTE="${REMOTE:-gdrive:hidden-op}"
grep -q '^HIDDENOP_REMOTE=' .env || echo "HIDDENOP_REMOTE=$REMOTE" >> .env
RNAME="${REMOTE%%:*}"

step "Google Drive (rclone remote '$RNAME')"
if rclone listremotes | grep -qx "$RNAME:"; then
  echo "Already set up."
else
  cat <<MSG
This server has no browser, so you sign in to Google on your PC instead.
On your Windows PC, in PowerShell, run:

    winget install Rclone.Rclone
    rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"

(Open a NEW PowerShell window after installing if 'rclone' isn't found.)
A browser opens: sign in with the Google account that has your 2 TB, click Allow.
PowerShell then prints a line starting with {"access_token":...} between
"Paste the following into your remote machine --->" and "<---End paste".
Copy that whole line (from { to }) and paste it here.
MSG
  read -rp "Token: " TOKEN
  [[ "$TOKEN" == \{*\} ]] || { echo "Expected the {...} line from rclone authorize." >&2; exit 1; }
  # scope drive.file: rclone can only see files it created itself, not the rest of your Drive.
  rclone config create "$RNAME" drive scope=drive.file config_refresh_token=false token="$TOKEN" </dev/null >/dev/null
fi
rclone mkdir "$REMOTE/raw"
rclone lsd "$REMOTE" >/dev/null && echo "Drive works: $REMOTE"

step "Installing systemd services"
for f in hiddenop-collector@.service hiddenop-archive.service hiddenop-archive.timer \
         hiddenop-snapshot.service hiddenop-snapshot.timer; do
  sed -e "s|@USER@|$USER_NAME|g" -e "s|@DIR@|$DIR|g" "deploy/$f" | sudo tee "/etc/systemd/system/$f" >/dev/null
done
chmod +x deploy/*.sh
# Keep a month of logs at most.
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\nMaxRetentionSec=1month\n' | sudo tee /etc/systemd/journald.conf.d/hiddenop.conf >/dev/null
sudo systemctl restart systemd-journald
sudo systemctl daemon-reload

for p in $PLATFORMS; do
  sudo systemctl enable "hiddenop-collector@$p" >/dev/null 2>&1
  sudo systemctl restart "hiddenop-collector@$p"
done
sudo systemctl enable --now hiddenop-archive.timer hiddenop-snapshot.timer >/dev/null 2>&1

sleep 5
step "Done"
for p in $PLATFORMS; do printf '  %-6s %s\n' "$p" "$(systemctl is-active "hiddenop-collector@$p")"; done
cat <<MSG

Collectors run 24/7 and restart themselves after crashes and reboots.
Raw match JSON goes to Drive ($REMOTE/raw) every 3 hours; a slim DB copy
($REMOTE/hiddenop-slim.db) is uploaded daily.

Useful commands:
  bash deploy/status.sh                          health check
  journalctl -u hiddenop-collector@na1 -f        live log for one region (Ctrl+C to leave)
  sudo systemctl stop 'hiddenop-collector@*'     stop collecting
  sudo systemctl start hiddenop-archive          archive to Drive now
MSG
