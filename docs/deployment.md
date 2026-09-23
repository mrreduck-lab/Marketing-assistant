# Deployment Policy

## Server isolation

Marketing Assistant is deployed as an isolated service on the RASCHINI server.

The server already hosts other projects. This project must not modify or overwrite existing applications.

## Rules

- Use a dedicated directory:

`/opt/marketing-assistant` or `/home/marketing-assistant`

- Use a dedicated Docker Compose project name:

`raschini-mail-assistant`

- Use unique container names:

`raschini-mail-reader`

- Do not modify existing nginx configurations.
- Do not install global packages affecting other applications.
- Do not share databases with other projects.
- Do not expose external ports unless explicitly required.

## First deployment checks

Before deployment:

```bash
docker ps
docker compose ls
```

Existing services must be identified before starting Marketing Assistant.

## Container policy

The service should use:

- its own environment variables;
- its own logs;
- its own restart policy;
- isolated dependencies.

The first Mail Reader version is an internal worker. It connects to Mail.ru through IMAP SSL and does not provide a public web interface.
