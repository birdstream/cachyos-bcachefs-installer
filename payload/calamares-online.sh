#!/usr/bin/env bash
set -Eeuo pipefail
[[ -f /usr/lib/calamares/modules/bcachefsjob/main.py ]] || {
    echo 'The custom bcachefs installer is missing.' >&2; exit 1;
}
sudo cp /usr/share/calamares/settings_online.conf /etc/calamares/settings.conf
log="$HOME/cachy-install.log"
printf 'Starting the bcachefs-enabled installer. Log: %s\n' "$log"
exec pkexec-wrapper calamares -D6 >> "$log" 2>&1
