#!/usr/bin/env bash
# Installs anistash inside a Debian LXC (12 or newer). ct/anistash.sh on the Proxmox host
# runs this for you; to run it by hand inside a fresh privileged Debian container:
#
#   curl -fsSL https://raw.githubusercontent.com/JustinMarxGit/anistash/main/install/install.sh | bash
#
# Only the container is set up here. The app starts unconfigured: open the web UI
# (or run anistash-setup) to create the admin account and add download locations.
#
# Environment: ANISTASH_PORT (8000), ANISTASH_REPO (JustinMarxGit/anistash), ANISTASH_REF (newest release),
#              TZ (keep current), ANISTASH_VERBOSE (0)
set -euo pipefail

PORT="${ANISTASH_PORT:-8000}"
REPO="${ANISTASH_REPO:-JustinMarxGit/anistash}"
REF="${ANISTASH_REF:-}"
APP=/opt/anistash
VERBOSE="${ANISTASH_VERBOSE:-0}"
LOG=/var/log/anistash-install.log

GN=$'\e[32m' RD=$'\e[31m' BL=$'\e[36m' CL=$'\e[0m'
step=""
info() { step="$*"; echo -e " ${BL}…${CL} $*"; }
ok() { echo -e " ${GN}✓${CL} $*"; }
fail() { echo -e " ${RD}✗${CL} Failed while: $step. Details: $LOG" >&2; exit 1; }
trap fail ERR
quiet() { if [[ $VERBOSE == 1 ]]; then "$@"; else "$@" >>"$LOG" 2>&1; fi; }

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -f /etc/debian_version ]] || { echo "AniStash's installer supports Debian only." >&2; exit 1; }
[[ $PORT =~ ^[0-9]+$ ]] && ((PORT > 0 && PORT < 65536)) || { echo "ANISTASH_PORT must be a port number." >&2; exit 1; }
: >"$LOG"

# Same fixes the community scripts apply: Debian 13 templates can leave / owned by
# nobody, and /etc without o+x stops apt's _apt user from reading resolv.conf
[[ "$(stat -c %U /)" == root ]] || chown root:root /
(( 0$(stat -c %a /etc) & 1 )) || chmod o+x /etc

if [[ -n ${TZ:-} && -f /usr/share/zoneinfo/$TZ ]]; then
    info "Setting timezone to $TZ"
    ln -sf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" >/etc/timezone
    ok "Timezone $TZ"
fi

info "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
quiet apt-get update
quiet apt-get install -y --no-install-recommends \
    ca-certificates curl git sudo python3 python3-venv ffmpeg cifs-utils nfs-common smbclient \
    sed grep procps whiptail
ok "System packages"

info "Installing yt-dlp"
# Debian's yt-dlp goes stale within weeks; the release binary can update itself
quiet curl -fsSL -o /usr/local/bin/yt-dlp https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp
chmod 0755 /usr/local/bin/yt-dlp
ok "yt-dlp $(/usr/local/bin/yt-dlp --version)"

info "Installing ani-cli"
quiet curl -fsSL -o /usr/local/bin/ani-cli https://raw.githubusercontent.com/pystardust/ani-cli/master/ani-cli
chmod 0755 /usr/local/bin/ani-cli
ok "ani-cli $(/usr/local/bin/ani-cli -V 2>/dev/null | tail -n1)"

info "Creating the AniStash user and folders"
id anistash &>/dev/null || useradd --system --home-dir /var/lib/anistash --shell /usr/sbin/nologin anistash
install -d -m 0750 -o anistash -g anistash /var/lib/anistash
install -d -m 0755 -o root -g root /etc/anistash /mnt/anistash
install -d -m 0700 -o root -g root /etc/anistash/creds
ok "User and folders"

info "Downloading AniStash"
if [[ -z $REF ]]; then
    REF="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" |
        python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null || true)"
    REF="${REF:-main}"  # no release published yet
fi
if [[ -d $APP/.git ]]; then
    quiet git -C "$APP" fetch --tags --force origin
else
    rm -rf "$APP"
    quiet git clone "https://github.com/$REPO.git" "$APP"
fi
quiet git -C "$APP" checkout --force "$REF"
chown -R root:root "$APP"
ok "AniStash $(cat "$APP/VERSION") ($REF)"

info "Writing settings"
cat >/etc/anistash/anistash.env <<EOF
# anistash service settings. Change the port here, then: systemctl restart anistash
ANISTASH_PORT=$PORT
ANISTASH_DATA_DIR=/var/lib/anistash
ANISTASH_REPO=$REPO
EOF
ok "/etc/anistash/anistash.env"

info "Installing the app and services"
quiet bash "$APP/install/deploy.sh"
quiet systemctl restart anistash.service
for _ in $(seq 1 30); do curl -fs "http://127.0.0.1:$PORT/api/status" >/dev/null && break; sleep 1; done
curl -fs "http://127.0.0.1:$PORT/api/status" >/dev/null
ok "AniStash is running"

ip="$(hostname -I | awk '{print $1}')"
echo
echo -e " ${GN}AniStash is installed.${CL} Open ${BL}http://${ip}:${PORT}${CL} to finish setup,"
echo "   or run anistash-setup in this console."
echo "   Update later with: anistash-update"
