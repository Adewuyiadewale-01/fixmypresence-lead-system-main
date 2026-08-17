# Security policy

## Supported configuration

Credentials, webhook tokens, service-account files, browser sessions, runtime state, and collected lead/contact data must not be committed to this repository.

Keep the dashboard bound to loopback or place it behind an authenticated private access layer. The dashboard contains operational controls and should not be exposed directly to the public internet.

## Reporting a vulnerability

Do not open a public issue for a suspected exposed secret or security vulnerability. Contact the repository maintainer through a private channel and include the affected file path, commit, and a minimal reproduction where possible.
