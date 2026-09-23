# Mail Reader Health Check

## Purpose

Simple operational checks for the Mail Reader service.

## Expected state

- Container is running.
- IMAP connection to Mail.ru is available.
- Authentication uses Mail.ru application password.
- No mail archive is stored locally.
- No public network port is exposed.

## Troubleshooting order

1. Check container status.
2. Check service logs.
3. Check environment variables.
4. Check IMAP connectivity.

Do not store credentials in GitHub or logs.
