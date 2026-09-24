#!/usr/bin/env bash
# Dedicated R Mail only. No mailbox data or credentials are echoed.
set -Eeuo pipefail
umask 077

readonly revision='8a6073ed3d2ef3e706aa258c55c9fd0278e35b39'
readonly repo='mrreduck-lab/Marketing-assistant'
readonly base='/opt/raschini-mail-mcp'
readonly target="${base}/mcp_server"
readonly python="${base}/.venv-mcp/bin/python"
readonly unit='raschini-mail-mcp.service'
readonly source="https://raw.githubusercontent.com/${repo}/${revision}"
readonly files=(server.py mail_actions.py calendar_actions.py mailbox_folders.py)

[[ $EUID -eq 0 ]] || { echo 'Run as root on the dedicated R Mail host' >&2; exit 1; }
[[ -f "${target}/server.py" && -x "$python" ]] || { echo 'Unexpected MCP path; unchanged' >&2; exit 1; }
systemctl is-active --quiet "$unit" || { echo 'R Mail unit inactive; unchanged' >&2; exit 1; }

staging=$(mktemp -d)
backup=$(mktemp -d "${base}/mcp-v2-backup.XXXXXXXX")
cleanup() { rm -rf "$staging"; }
trap cleanup EXIT
mkdir -p "$staging/mcp_server" "$staging/tests"
for file in "${files[@]}"; do
    curl --fail --silent --show-error --location --retry 2 \
        "${source}/mcp_server/${file}" -o "$staging/mcp_server/${file}"
done
for file in test_mail_actions.py test_calendar_actions.py test_mailbox_folders.py; do
    curl --fail --silent --show-error --location --retry 2 \
        "${source}/tests/${file}" -o "$staging/tests/${file}"
done
"$python" -m compileall -q "$staging/mcp_server" "$staging/tests"
"$python" -m unittest discover -s "$staging/tests" -v

for file in "${files[@]}"; do
    if [[ -f "${target}/${file}" ]]; then
        cp -p "${target}/${file}" "${backup}/${file}"
    else
        touch "${backup}/.${file}.previously_absent"
    fi
done

rollback() {
    for file in "${files[@]}"; do
        if [[ -f "${backup}/.${file}.previously_absent" ]]; then
            rm -f "${target}/${file}"
        else
            cp -p "${backup}/${file}" "${target}/${file}"
        fi
    done
    systemctl restart "$unit" || true
    echo "R Mail rollback from ${backup}; check service logs" >&2
}
for file in "${files[@]}"; do
    install -m 0644 "$staging/mcp_server/$file" "$target/$file"
done
if ! systemctl restart "$unit" || ! systemctl is-active --quiet "$unit"; then
    rollback
    exit 1
fi

echo "R Mail v2 code installed and dedicated unit active."
echo "Rollback copy: ${backup}"
echo "SMTP credentials untouched; test with a dedicated mailbox before sending live mail."
echo "Refresh R Mail tools in ChatGPT after validating the provider folders."
