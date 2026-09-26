# R Mail: private MCP service

The live service uses an outbound Secure MCP Tunnel to reach this stdio server.
Credentials and work history stay on the private host. No public listener is required.

## Tools

- `list_recent_mail` and `find_mail`: INBOX headers.
- `list_mail_calendars` and `list_mail_calendar_events(start_date,end_date,calendar_id,limit)`: read-only CalDAV calendars and events. The end date is exclusive; default timezone is Europe/Moscow. The server returns recurrence rules without expanding them into each occurrence.
- `read_mail(uid)`: text of a selected message, sender, recipients and attachment filenames; uses `BODY.PEEK[]` with INBOX selected `readonly=True`. Attachments are not returned.
- `read_mail_thread(uid)`: related messages in the scanned INBOX window, using Message-ID/References; identifies missing Sent-folder context.
- `draft_mail_reply(uid,body)`: creates a local, unsent draft to the single Reply-To or From address. Show the returned full recipient, subject and body to the user.
- `send_mail_reply(draft_id,approval)`: sends *only* after the user explicitly approves that exact preview. The model passes `SEND <draft_id>` after approval. No retry after an uncertain SMTP result: first inspect the Sent folder.
- `record_mail_work` and `get_mail_work_report`: metadata-only journal and cumulative reports of actions performed with R Mail. They do not backfill past work or store message bodies.
- `add_auto_reply_rule`, `get_auto_reply_rules`, `change_auto_reply_rule`, `execute_auto_reply_check`: an initially disabled rule requires one exact sender address, a subject phrase and a fixed response. Show the rule to the user before explicit approval with `ENABLE <rule_id>`. Only messages arriving after the rule is enabled can be answered. A daily cap defaults to five. The worker does not run in the background unless a private timer is configured.

Auto reply rules deliberately do not generate freeform AI replies. For partnership negotiations and other nuanced correspondence, review the full chain and approve an individual draft. Do not create a broad catchall rule.

## Private host setup

Keep the existing `MAIL_USER`, `MAIL_PASSWORD`, `MAIL_HOST`, `MAIL_PORT` environment. For sending, set `SMTP_USER` to the exact same mailbox as `MAIL_USER`, a valid `SMTP_PASSWORD` application password, and optionally `SMTP_HOST` (default `smtp.mail.ru`), `SMTP_PORT` (default `465`). There is no send capability without both SMTP credentials.

For calendar reading, add `CALDAV_USER` (full Mail.ru mailbox address) and `CALDAV_PASSWORD` (a separately issued external-app password) to the existing service's protected environment. Optionally set `CALDAV_TIMEZONE=Europe/Moscow`; the endpoint defaults to `https://calendar.mail.ru/`. Do not paste a password into ChatGPT or check it into GitHub. `CALDAV_URL` can change the initial discovery path but is restricted to HTTPS on `calendar.mail.ru`. Verify that the corporate account has CalDAV enabled before promising calendar access. CalDAV responses are not stored in the local work journal.

Set `MAIL_STATE_DIR` to a dedicated directory owned by the service account with mode `0700` (default `/var/lib/raschini-mail-mcp`). The SQLite file stores report metadata and temporary reply bodies for up to 24 hours. It must never be committed or backed up to a shared repository. The file is mode `0600`; the service process should use `UMask=0077`. Restrict access to the tunnel and MCP app to the intended user or workspace.

Deploy `server.py` and `mail_actions.py` together in the same directory, retaining the existing MCP venv. Test local read and SMTP with a test message before enabling any automatic rule. A tunnel client/service restart may be required for the new tool list to be discovered in ChatGPT. The prior header tools are unchanged.

The one-command host rollout is `scripts/deploy_r_mail.sh` in this branch. It checks the known service path, downloads code at a fixed commit, runs isolated tests, backs up the old server and rolls back if the dedicated service cannot restart. If the host uses another path, it stops without changing the service; inspect that host layout before adapting it. It does not enable SMTP or automatic sending.

For optional automation, invoke `run_auto_replies()` from a scheduled private process using the *same* credential environment and state directory, or call `execute_auto_reply_check` from an approved scheduler. Start with one narrowly scoped reviewed rule. Rule changes and mail actions appear in `get_mail_work_report`. No rule is enabled by deploying this release.

Limitations: INBOX only for reading; attachments are listed but not opened; `read_mail` returns at most 50,000 body characters from a message up to 2 MB; a thread search covers only recent INBOX and not Sent. A failed SMTP call can have an uncertain outcome, and the service refuses to automatically retry it.

## Raschini pilot: Sent and SMTP diagnostics (proposed change)

New tools: `list_mail_folders`, `list_sent_mail(limit,query)`, `read_sent_mail(uid)`, and `check_smtp_configuration`. They read Sent without marking messages as read and return no passwords. `list_sent_mail` scans at most 500 latest Sent headers. If the server does not expose exactly one \\Sent special-use folder, set `MAIL_SENT_FOLDER` to the exact folder name returned by `list_mail_folders`. Sent UIDs cannot be passed to INBOX-only `read_mail` or `draft_mail_reply`.

SMTP diagnostics only verify environment variables. Sending still requires `SMTP_USER` equal to `MAIL_USER`, `SMTP_PASSWORD` (app password), and an approved draft. A configured status does not prove a successful SMTP login or delivery. Do not paste credentials into chat. Deploy only after tests and a private-server configuration check; this branch is not automatically deployed.
