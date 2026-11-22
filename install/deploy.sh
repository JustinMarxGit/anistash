#!/usr/bin/env bash
# Installs the checked-out code in /opt/anistash into the system: Python venv, root
# helper, sudoers rule, systemd units and the anistash-setup / anistash-update commands.
# Idempotent; run by install.sh on first install and by anistash-update after every update.
set -euo pipefail

APP=/opt/anistash
LIB=/usr/local/lib/anistash

[[ $EUID -eq 0 ]] || { echo "deploy.sh must run as root" >&2; exit 1; }

# System packages added in later versions, so updates bring them to older installs too
missing=()
for pkg in smbclient cifs-utils nfs-common ffmpeg whiptail; do
    dpkg -s "$pkg" &>/dev/null || missing+=("$pkg")
done
if ((${#missing[@]})); then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}"
fi

# Python dependencies (anistash-update rolls the code back and reruns this if it fails)
[[ -x $APP/venv/bin/python3 ]] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet -r "$APP/requirements.txt"

# Key for secrets encrypted at rest (the media server token). Readable by the service
# only; kept outside the data folder so a copy of the database alone is useless.
if [[ ! -s /etc/anistash/secret.key ]]; then
    install -d -m 0755 /etc/anistash
    (umask 077 && python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())' >/etc/anistash/secret.key)
fi
chown root:anistash /etc/anistash/secret.key
chmod 0640 /etc/anistash/secret.key

# The helper runs as root through sudo, so it must not be writable by the app user
install -d -m 0755 -o root -g root "$LIB"
install -m 0755 -o root -g root "$APP/helper/anistash-helper" "$LIB/anistash-helper"
install -m 0755 -o root -g root "$APP/install/anistash-setup" /usr/local/bin/anistash-setup
install -m 0755 -o root -g root "$APP/install/anistash-update" /usr/local/bin/anistash-update

sudoers=$(mktemp)
cat >"$sudoers" <<EOF
# anistash may run only its own validated helper as root
anistash ALL=(root) NOPASSWD: $LIB/anistash-helper
EOF
visudo -cqf "$sudoers"
install -m 0440 -o root -g root "$sudoers" /etc/sudoers.d/anistash
rm -f "$sudoers"

install -m 0644 "$APP"/install/systemd/*.service "$APP"/install/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet anistash.service anistash-tools-update.timer
systemctl start --quiet anistash-tools-update.timer
