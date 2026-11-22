# Security

AniStash runs on your own network, but it stores passwords, mounts network shares and
holds a Jellyfin/Plex API key, so security problems matter. Thanks for reporting them.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository. Include what you found, how to
reproduce it, and which version (`Settings` shows it, or `cat /opt/anistash/VERSION`).

You'll get an answer as soon as possible. Fixes ship as a normal release, which installs
through the usual update path (automatic updates, the Update button, or `anistash-update`).

## In scope

- The web app and its API (`app/`): authentication, sessions, roles (admin / member / viewer),
  CSRF and cross-site protections, anything one user can do to another.
- The root helper (`helper/anistash-helper`), the only thing the app may run as root.
- The install, update and setup scripts (`ct/`, `install/`).

## Out of scope

- ani-cli, yt-dlp, ffmpeg, Jellyfin, Plex, AniList and the other sites AniStash talks to.
  Please report those to their own projects.
- Anything that needs root in the container or on the Proxmox host to begin with.
- Exposing AniStash directly to the internet. It's built for a home network; put it behind
  a VPN (WireGuard, Tailscale) or an authenticating reverse proxy if you need remote access.

## How AniStash protects itself (summary)

- Passwords hashed with scrypt; session tokens stored only as SHA-256 hashes.
- Login lockout after repeated failures, per user and per address.
- Every API route checks the caller's role on the server.
- State-changing requests must come from the app itself (custom header + origin check).
- Strict Content-Security-Policy and security headers on every response.
- The Jellyfin/Plex key and the MyAnimeList Client ID are stored encrypted.
- An audit log of logins and changes (Settings → Security log).

See the README section *Users and security* for details.
