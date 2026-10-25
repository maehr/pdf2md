# Security Policy

## Supported Versions

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| < 0.2 | No |

Security fixes apply to the latest 0.2.x release.

## Reporting a Vulnerability

Report vulnerabilities through GitHub private vulnerability reporting:

<https://github.com/maehr/pdf2md/security/advisories/new>

Do not open a public issue for a vulnerability.
Do not post exploit details in public discussions.

Include these details in the report:

- the affected version or commit;
- the macOS version, chip, Python version, and `uv` version;
- steps to reproduce the vulnerability;
- the expected and observed impact;
- known mitigations or workarounds.

The maintainer acknowledges reports within 7 days.
The maintainer provides an initial assessment within 30 days.
The maintainer agrees on a disclosure date with the reporter.
The target is disclosure within 90 days after the report.

## Scope

This policy covers the `pdf2md` command and the optional `pdf2md-mcp` server.
It also covers the packaging and CI configuration in this repository.

These documented limits are not vulnerabilities:

- The MCP root check is not an operating-system sandbox.
- The server keeps the local user's file permissions.
- An MCP client can forward returned Markdown to a remote provider.
- Client timeouts do not guarantee cancellation.

Report dependency vulnerabilities to the affected upstream project.
You can also report them here when the issue affects pdf2md.

## Safe Use

Set `PDF2MD_MCP_ROOT` to the smallest directory tree that you need.
Keep model weights and converted documents in trusted local storage.
Review the data policy of your MCP client before you use remote models.
