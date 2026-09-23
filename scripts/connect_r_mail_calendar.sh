#!/usr/bin/env bash
# One-block private host setup. Never paste the application password into chat.
set -Eeuo pipefail

readonly unit='raschini-mail-mcp.service'
readonly deploy_revision='534eadd9ee321a462d497669560dc422dd13ea40'
readonly calendar_environment='/etc/raschini-mail-mcp-calendar.env'
readonly override_dir='/etc/systemd/system/raschini-mail-mcp.service.d'
readonly override="${override_dir}/calendar.conf"
readonly calendar_user="${CALDAV_USER:-marketing@raschini.com}"

if [[ $EUID -ne 0 || ! -t 0 ]]; then
  echo 'Run this script as root from an interactive terminal on the mail server.' >&2
  exit 1
fi

stage=$(mktemp -d)
chmod 0700 "$stage"
trap 'rm -rf "$stage"' EXIT
curl -fsSL "https://raw.githubusercontent.com/mrreduck-lab/Marketing-assistant/${deploy_revision}/scripts/deploy_r_mail.sh" -o "${stage}/deploy.sh"
bash "${stage}/deploy.sh"

if [[ -f "$calendar_environment" ]]; then
  cp -p "$calendar_environment" "${stage}/previous-calendar.env"
fi
if [[ -f "$override" ]]; then
  cp -p "$override" "${stage}/previous-calendar.conf"
fi

python3 - "$calendar_environment" "$calendar_user" <<'PY'
import getpass
import os
import sys
import tempfile

path, user = sys.argv[1:]
if not user or '\n' in user or '\r' in user:
    raise SystemExit('Invalid calendar mailbox')
password = getpass.getpass('Пароль приложения для календаря Mail.ru (ввод скрыт): ')
if not password or '\n' in password or '\r' in password:
    raise SystemExit('Invalid calendar application password')
def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
fd, temp = tempfile.mkstemp(prefix='.raschini-mail-calendar-', dir=os.path.dirname(path))
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        os.fchmod(out.fileno(), 0o600)
        out.write('CALDAV_USER=' + quote(user) + '\n')
        out.write('CALDAV_PASSWORD=' + quote(password) + '\n')
    os.replace(temp, path)
finally:
    if os.path.exists(temp):
        os.unlink(temp)
PY

install -d -m 0755 "$override_dir"
printf '[Service]\nEnvironmentFile=%s\n' "$calendar_environment" > "$override"
chmod 0644 "$override"
systemctl daemon-reload
if ! systemctl restart "$unit" || ! systemctl is-active --quiet "$unit"; then
  if [[ -f "${stage}/previous-calendar.env" ]]; then
    cp -p "${stage}/previous-calendar.env" "$calendar_environment"
  else
    rm -f "$calendar_environment"
  fi
  if [[ -f "${stage}/previous-calendar.conf" ]]; then
    cp -p "${stage}/previous-calendar.conf" "$override"
  else
    rm -f "$override"
  fi
  systemctl daemon-reload
  systemctl restart "$unit" || true
  echo 'Calendar environment rollback completed after a failed service restart.' >&2
  exit 1
fi
echo 'R Mail service is active. Calendar credentials are stored in a root-only service environment.'
echo 'Calendar login still needs a live list_mail_calendars check from ChatGPT.'
