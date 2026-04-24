# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in claude-code-proxy, please **do not** open a public issue.

Instead, email **seanbabalala@users.noreply.github.com** with:

- A description of the vulnerability
- Steps to reproduce
- Any relevant logs or screenshots

I'll respond within 72 hours and work with you to resolve the issue before any public disclosure.

## Scope

This proxy handles API keys and forwards HTTP traffic. Security-relevant areas include:

- Authentication token handling (`GATEWAY_API_KEY`, `UPSTREAM_API_KEY`)
- Request/response logging (may contain sensitive content)
- Upstream URL validation
