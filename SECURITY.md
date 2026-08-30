# Security Policy

## Supported versions

Only the latest release (the tip of the `main` branch) is supported. There are
no backports.

## Scope

Eclipse Cleaner is a local tool: it processes video files on your machine and
its review viewer binds to `127.0.0.1` only. Still, security issues are taken
seriously — for example:

- the viewer becoming reachable from other machines, or serving files outside
  the expected directories;
- unsafe handling of file paths or of the analysis/decision JSON files;
- a crafted video file triggering dangerous behavior beyond a simple crash.

## Reporting a vulnerability

Please **do not open a public issue** for a vulnerability. Instead, use
GitHub's private reporting: go to the repository's **Security** tab and click
**Report a vulnerability**
([direct link](https://github.com/bebkill/Eclipse-Cleaner/security/advisories/new)).

You should get a first reply within a week. This is a spare-time project, so
fixes may take a little longer — but reports are always appreciated.
