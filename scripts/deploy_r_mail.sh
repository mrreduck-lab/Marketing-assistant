#!/usr/bin/env bash
# Run on the private mail host as root. No credentials are printed or copied.
set -Eeuo pipefail

readonly revision='0890c62185c3142aad50bb857033b5ad0a506533'
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
curl -fsSL "${source}/mcp_server/search_actions.py" -o "${staging}/mcp_server/search_actions.py"
curl -fsSL "${source}/mcp_server/draft_actions.py" -o "${staging}/mcp_server/draft_actions.py"
curl -fsSL "${source}/mcp_server/attachment_actions.py" -o "${staging}/mcp_server/attachment_actions.py"
curl -fsSL "${source}/mcp_server/mail_actions.py" -o "${staging}/mcp_server/mail_actions.py"
curl -fsSL "${source}/mcp_server/calendar_actions.py" -o "${staging}/mcp_server/calendar_actions.py"
curl -fsSL "${source}/mcp_server/sent_actions.py" -o "${staging}/mcp_server/sent_actions.py"
curl -fsSL "${source}/tests/test_mail_actions.py" -o "${staging}/tests/test_mail_actions.py"
curl -fsSL "${source}/tests/test_search_actions.py" -o "${staging}/tests/test_search_actions.py"
curl -fsSL "${source}/tests/test_draft_actions.py" -o "${staging}/tests/test_draft_actions.py"
curl -fsSL "${source}/tests/test_attachment_actions.py" -o "${staging}/tests/test_attachment_actions.py"
curl -fsSL "${source}/tests/test_calendar_actions.py" -o "${staging}/tests/test_calendar_actions.py"
curl -fsSL "${source}/tests/test_sent_actions.py" -o "${staging}/tests/test_sent_actions.py"
"$python" -m pip install "pypdf>=5,<7"
"$python" -c 'import mcp'
PYTHONPATH="${staging}/mcp_server" "$python" -m unittest discover -s "${staging}/tests" -q
"$python" -m py_compile "${staging}/mcp_server/server.py" "${staging}/mcp_server/mail_actions.py" "${staging}/mcp_server/calendar_actions.py" "${staging}/mcp_server/sent_actions.py" "${staging}/mcp_server/attachment_actions.py" "${staging}/mcp_server/draft_actions.py" "${staging}/mcp_server/search_actions.py"

cp -p "${target}/server.py" "${backup}/server.py"
if [[ -f "${target}/search_actions.py" ]]; then cp -p "${target}/search_actions.py" "${backup}/search_actions.py"; fi
if [[ -f "${target}/draft_actions.py" ]]; then cp -p "${target}/draft_actions.py" "${backup}/draft_actions.py"; fi
if [[ -f "${target}/attachment_actions.py" ]]; then cp -p "${target}/attachment_actions.py" "${backup}/attachment_actions.py"; fi
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
install -m 0644 "${staging}/mcp_server/search_actions.py" "${target}/search_actions.py"
install -m 0644 "${staging}/mcp_server/draft_actions.py" "${target}/draft_actions.py"
install -m 0644 "${staging}/mcp_server/attachment_actions.py" "${target}/attachment_actions.py"
install -m 0644 "${staging}/mcp_server/sent_actions.py" "${target}/sent_actions.py"
install -m 0644 "${staging}/mcp_server/mail_actions.py" "${target}/mail_actions.py"
install -m 0644 "${staging}/mcp_server/calendar_actions.py" "${target}/calendar_actions.py"
service_user=$(systemctl show "$unit" -p User --value)
service_user=${service_user:-root}
install -d -o "$service_user" -m 0700 /var/lib/raschini-mail-mcp
if ! systemctl restart "$unit" || ! systemctl is-active --quiet "$unit"; then
  cp -p "${backup}/server.py" "${target}/server.py"
  if [[ -f "${backup}/search_actions.py" ]]; then cp -p "${backup}/search_actions.py" "${target}/search_actions.py"; else rm -f "${target}/search_actions.py"; fi
  if [[ -f "${backup}/draft_actions.py" ]]; then cp -p "${backup}/draft_actions.py" "${target}/draft_actions.py"; else rm -f "${target}/draft_actions.py"; fi
  if [[ -f "${backup}/attachment_actions.py" ]]; then cp -p "${backup}/attachment_actions.py" "${target}/attachment_actions.py"; else rm -f "${target}/attachment_actions.py"; fi
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
