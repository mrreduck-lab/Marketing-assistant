# Private read-only Mail MCP server

This is an on-demand **stdio** MCP server for the corporate Mail.ru INBOX.
It runs separately from the existing mail-reader systemd unit, without any
listening network port, Docker changes, or nginx changes.

Tools:
- `list_recent_mail(limit=20)`: last 1-50 message headers.
- `find_mail(query, scan_limit=100)`: match sender or subject in the last 1-200 messages.

Both tools select INBOX with `readonly=True`, use IMAP UIDs and
`BODY.PEEK[HEADER.FIELDS ...]`, do not change Seen flags, and never
fetch bodies or attachments. They keep no local mail copy. This release
does **not** send mail or claim to summarize full correspondence.

## Local setup on the RASCHINI host

Use a dedicated Python virtual environment inside this project; do not
install anything globally and do not modify the running mail-reader unit.

```bash
cd /opt/raschini-marketing-assistant
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r mcp_server/requirements.txt
```

The process needs `MAIL_USER` and `MAIL_PASSWORD` in its environment.
Reuse the **existing, local** environment file for the mail reader through
a dedicated launcher/service. Never commit it, print its contents, or copy
its values into a GitHub file or a `tunnel-client` command. Verify its
path and permissions on the host before configuring that launcher. The
optional `MAIL_HOST` and `MAIL_PORT` default to `imap.mail.ru:993`.

Configure OpenAI Secure MCP Tunnel with a stdio command that starts
`.venv-mcp/bin/python /opt/raschini-marketing-assistant/mcp_server/server.py`
with those environment variables. Store the tunnel runtime key in a
protected local environment file and use a **separate** systemd service
for tunnel-client. The tunnel requires a Platform `tunnel_id` associated
with the intended ChatGPT workspace. Run `tunnel-client doctor` and
test tool discovery before using ChatGPT. The skill-only private plugin
must be connected to this tunnel after it has been tested.

No existing service should be restarted as part of setup. On the first
test, request a few headers and confirm their contents in ChatGPT before
considering message bodies or send capability.
