#!/usr/bin/env bash
# Installs the checked-out code in /opt/anidl into the system: Python venv, root
# helper, sudoers rule, systemd units and the anidl-setup / anidl-update commands.
# Idempotent; run by install.sh on first install and by anidl-update after every update.
set -euo pipefail

APP=/opt/anidl
LIB=/usr/local/lib/anidl

[[ $EUID -eq 0 ]] || { echo "deploy.sh must run as root" >&2; exit 1; }

# Python dependencies (anidl-update rolls the code back and reruns this if it fails)
[[ -x $APP/venv/bin/python3 ]] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet -r "$APP/requirements.txt"

# The helper runs as root through sudo, so it must not be writable by the app user
install -d -m 0755 -o root -g root "$LIB"
install -m 0755 -o root -g root "$APP/helper/anidl-helper" "$LIB/anidl-helper"
install -m 0755 -o root -g root "$APP/install/anidl-setup" /usr/local/bin/anidl-setup
install -m 0755 -o root -g root "$APP/install/anidl-update" /usr/local/bin/anidl-update

sudoers=$(mktemp)
cat >"$sudoers" <<EOF
# anidl may run only its own validated helper as root
anidl ALL=(root) NOPASSWD: $LIB/anidl-helper
EOF
visudo -cqf "$sudoers"
install -m 0440 -o root -g root "$sudoers" /etc/sudoers.d/anidl
rm -f "$sudoers"

install -m 0644 "$APP"/install/systemd/*.service "$APP"/install/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet anidl.service anidl-tools-update.timer
systemctl start --quiet anidl-tools-update.timer
