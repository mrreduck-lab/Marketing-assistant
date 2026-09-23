#!/usr/bin/env bash
# Run on the private mail host as root. No credentials are printed or copied.
set -Eeuo pipefail

readonly revision='47571b40d40998d9762e9a71f011b2efba76792a'
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
curl -fsSL "${source}/tests/test_mail_actions.py" -o "${staging}/tests/test_mail_actions.py"
"$python" -c 'import mcp'
"$python" -m unittest discover -s "${staging}/tests" -q
"$python" -m py_compile "${staging}/mcp_server/server.py" "${staging}/mcp_server/mail_actions.py"

cp -p "${target}/server.py" "${backup}/server.py"
if [[ -f "${target}/mail_actions.py" ]]; then
  cp -p "${target}/mail_actions.py" "${backup}/mail_actions.py"
fi
install -m 0644 "${staging}/mcp_server/server.py" "${target}/server.py"
install -m 0644 "${staging}/mcp_server/mail_actions.py" "${target}/mail_actions.py"
service_user=$(systemctl show "$unit" -p User --value)
service_user=${service_user:-root}
install -d -o "$service_user" -m 0700 /var/lib/raschini-mail-mcp
if ! systemctl restart "$unit" || ! systemctl is-active --quiet "$unit"; then
  cp -p "${backup}/server.py" "${target}/server.py"
  if [[ -f "${backup}/mail_actions.py" ]]; then
    cp -p "${backup}/mail_actions.py" "${target}/mail_actions.py"
  else
    rm -f "${target}/mail_actions.py"
  fi
  systemctl restart "$unit" || true
  echo "Deployment failed; previous files restored from ${backup}." >&2
  exit 1
fi
echo "R Mail code installed; dedicated service active. Rollback copy: ${backup}"
echo 'SMTP sending remains disabled until SMTP_USER and SMTP_PASSWORD are configured.'
