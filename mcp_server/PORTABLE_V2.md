# R Mail portable mailboxes v2 — staged rollout

This branch is **not** deployed to the RASCHINI production mail service.
It extends the legacy single-mailbox MCP without changing the existing SMTP
credentials, tunnel, Mail.ru login, or other server processes.

## Behavior

- `list_mail_folders` discovers mailboxes and special-use flags through IMAP LIST.
- `list_sent_mail`, `find_sent_mail`, `read_sent_mail` read the real provider Sent
  mailbox; no read flags or attachment bytes are changed or exported.
- `draft_mail_reply` appends a real MIME message to provider Drafts and verifies
  its Message-ID before returning success. Approval remains mandatory.
- `send_mail_reply` sends by SMTP **at most once** per approved draft.
  It checks the provider Sent folder for that Message-ID, appends the MIME
  message if absent, and verifies it again. If SMTP accepts but IMAP fails,
  the result is `smtp_accepted_sent_unverified`; **do not send twice**.
- After verified Sent, UIDPLUS UID EXPUNGE may remove the matching real Draft
  without expunging unrelated mail. If unavailable, the old draft remains and
  `draft_removed_from_mailbox=false` explains why. Never run a global EXPUNGE.
- No calendar event write support is claimed in this release.

## Server settings

The existing private service supplies MAIL_USER / MAIL_PASSWORD / MAIL_HOST /
MAIL_PORT; SMTP_USER and SMTP_PASSWORD must be set **privately** before
sending. Credentials must never enter Git, an AI conversation, or logs.

Optional settings:

```dotenv
MAIL_SENT_FOLDER=
MAIL_DRAFTS_FOLDER=
```

Leave unset if IMAP special-use folder flags identify folders. Otherwise
run `list_mail_folders`, inspect its returned names, and configure exact
display names in the private service environment. These values must match
IMAP LIST; R Mail refuses to invent mailbox names.

## Commercial product boundaries

This remains a *private single-tenant adapter*. It is **not** safe to publish
as a multi-user connector: the shared MAIL_USER/MAIL_PASSWORD and SQLite file
would allow cross-user exposure. Before any public install:

1. Extract provider adapters from the MCP tool layer. Do not put product or
   brand names in provider interfaces.
2. Resolve mailbox credentials by authenticated tenant + account identity;
   forbid ambient shared MAIL_USER from any multi-tenant execution path.
3. Use separately scoped and encrypted per-user credentials, preferably OAuth
   when supported. Rotate/revoke and audit credential access.
4. Replace process-global SQLite state with per-tenant persistence, encrypted
   message bodies, retention and deletion policies, migrations and backups.
5. Authorize every read/write against tenant + mailbox, with server-verified
   user identity; tool arguments may not select someone else's account.
6. Separate ChatGPT tool payload generation from the Russian-resident privacy
   boundary; never claim simple token replacement is legal anonymization.
7. Confirm provider terms, privacy basis, cross-border transfer and OpenAI
   publication/billing requirements with qualified counsel before launch.

## Rollout gates

1. Review PR, run all offline unit tests and compile checks on Python 3.12.
2. Back up only R Mail code and its private SQLite state. Do not back up or
   modify other application folders, containers or shared service units.
3. Deploy only the MCP Python files to a test copy first. Use a dedicated
   test mailbox and test calendar. Check Mail.ru-specific IMAP LIST output.
4. Confirm actual draft in Mail.ru UI, approval, SMTP acceptance, **one**
   message in Sent, safe Draft cleanup and behavior after IMAP failure.
5. Only then roll out to the dedicated `raschini-mail-mcp.service`.
   Refresh the developer app's tool schema after restart. Old conversations
   may still be forbidden by ChatGPT independently of this server.

SMTP may automatically save Sent asynchronously on some providers; IMAP
message-ID checks reduce duplication but cannot guarantee absence of races.
Validate that provider's behavior in step 4; consider provider-specific mode
before enabling commercial sending.
