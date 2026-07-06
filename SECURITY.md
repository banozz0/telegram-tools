# Security Policy

## Reporting Security Issues

Please do not open public issues for vulnerabilities or accidental secret exposure.

Report privately through GitHub security advisories for this repository, or contact the maintainer through a private channel listed on the repository owner profile.

## Sensitive Data

Do not include:

- Telegram API hashes
- bot tokens
- phone numbers
- `.env`
- `bots.json`
- `.telegram-tools/`
- `*.session*`
- exported private chat data
- screenshots that expose private chat names, members, phone numbers, or tokens

If you accidentally expose a secret, revoke or rotate it before reporting.

## Supported Versions

Until the first public release, security fixes target the default branch.
