#!/usr/bin/env bash
# Run on the private mail host as root. No credentials are printed or copied.
set -Eeuo pipefail

readonly revision='e945a68f51b4e51f1dac03ac2be3680295c4bfc0'
readonly base='/opt/raschini-mail-mcp'
readonly target="${base}/mcp_server"
readonly python="${base}/.venv-mcp/bin/python3"
readonly unit='raschini-mail-mcp.service'
readonly source="https://raw.githubusercontent.com/mrreduck-lab/Marketing-assistant/${revision}"

if [[ $EUID -ne 0 ]]; then
  echo 'Run as root on the mail host.' >&2
  exit 1
fi
if [[ ! -f "${target}/server.py" || ! -x "$python" ]]; then
  echo "Expected existing MCP at ${target}/server.py and venv at ${python}; nothing changed." >&2
  exit 1
fi
if ! systemctl is-active --quiet "$unit"; then
  echo "${unit} is not active; nothing changed." >&2
  exit 1
fi

staging=$(mktemp -d)
backup=$(mktemp -d "${base}/mcp-backup.XXXXXXXX")
cleanup() { rm -rf "$staging"; }
trap cleanup EXIT
mkdir -p "${staging}/mcp_server" "${staging}/tests"
curl -fsSL "${source}/mcp_server/server.py" -o "${staging}/mcp_server/server.py"
curl -fsSL "${source}/mcp_server/mail_actions.py" -o "${staging}/mcp_server/mail_actions.py"
curl -fsSL "${source}/mcp_server/calendar_actions.py" -o "${staging}/mcp_server/calendar_actions.py"
curl -fsSL "${source}/mcp_server/sent_actions.py" -o "${staging}/mcp_server/sent_actions.py"
curl -fsSL "${source}/tests/test_mail_actions.py" -o "${staging}/tests/test_mail_actions.py"
curl -fsSL "${source}/tests/test_calendar_actions.py" -o "${staging}/tests/test_calendar_actions.py"
curl -fsSL "${source}/tests/test_sent_actions.py" -o "${staging}/tests/test_sent_actions.py"
"$python" -c 'import mcp'
PYTHONPATH="${staging}/mcp_server" "$python" -m unittest discover -s "${staging}/tests" -q
"$python" -m py_compile "${staging}/mcp_server/server.py" "${staging}/mcp_server/mail_actions.py" "${staging}/mcp_server/calendar_actions.py" "${staging}/mcp_server/sent_actions.py"

cp -p "${target}/server.py" "${backup}/server.py"
if [[ -f "${target}/sent_actions.py" ]]; then
  cp -p "${target}/sent_actions.py" "${backup}/sent_actions.py"
fi
if [[ -f "${target}/mail_actions.py" ]]; then
  cp -p "${target}/mail_actions.py" "${backup}/mail_actions.py"
fi
if [[ -f "${target}/calendar_actions.py" ]]; then
  cp -p "${target}/calendar_actions.py" "${backup}/calendar_actions.py"
fi
install -m 0644 "${staging}/mcp_server/server.py" "${target}/server.py"
install -m 0644 "${staging}/mcp_server/sent_actions.py" "${target}/sent_actions.py"
install -m 0644 "${staging}/mcp_server/mail_actions.py" "${target}/mail_actions.py"
install -m 0644 "${staging}/mcp_server/calendar_actions.py" "${target}/calendar_actions.py"
service_user=$(systemctl show "$unit" -p User --value)
service_user=${service_user:-root}
install -d -o "$service_user" -m 0700 /var/lib/raschini-mail-mcp
if ! systemctl restart "$unit" || ! systemctl is-active --quiet "$unit"; then
  cp -p "${backup}/server.py" "${target}/server.py"
  if [[ -f "${backup}/sent_actions.py" ]]; then
    cp -p "${backup}/sent_actions.py" "${target}/sent_actions.py"
  else
    rm -f "${target}/sent_actions.py"
  fi
  if [[ -f "${backup}/mail_actions.py" ]]; then
    cp -p "${backup}/mail_actions.py" "${target}/mail_actions.py"
  else
    rm -f "${target}/mail_actions.py"
  fi
  if [[ -f "${backup}/calendar_actions.py" ]]; then
    cp -p "${backup}/calendar_actions.py" "${target}/calendar_actions.py"
  else
    rm -f "${target}/calendar_actions.py"
  fi
  systemctl restart "$unit" || true
  echo "Deployment failed; previous files restored from ${backup}." >&2
  exit 1
fi
echo "R Mail code installed; dedicated service active. Rollback copy: ${backup}"
echo 'SMTP can reuse MAIL_USER and MAIL_PASSWORD when explicit SMTP credentials are absent; test delivery before relying on it.'
echo 'Calendar can reuse MAIL_USER and MAIL_PASSWORD if CALDAV credentials are absent; create an approved test event to verify provider access.'
