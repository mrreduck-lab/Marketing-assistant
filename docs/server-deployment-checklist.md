# Server deployment checklist

## Before deployment

- Check existing Docker containers.
- Do not modify existing projects.
- Do not install global dependencies.
- Confirm free disk space.

## Marketing Assistant isolation

Project directory:

`/home/marketing-assistant`

Docker project name:

`raschini-mail-assistant`

Container:

`raschini-mail-reader`

## Network

- No public ports.
- No nginx changes.
- Outbound connection only to Mail.ru IMAP.

## Secrets

`.env` stays only on the server.

Never commit:

- passwords;
- tokens;
- mailbox content;
- logs containing email text.

## First launch

1. Clone repository.
2. Create `.env` from `.env.example`.
3. Start only this compose project.
4. Check container status.
5. Verify Mail.ru connection.
