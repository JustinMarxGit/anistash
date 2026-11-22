# AniStash

A self-hosted web UI for [ani-cli](https://github.com/pystardust/ani-cli). Browse and search anime from your phone or desktop, pick whole series, seasons or single episodes, and download them to your NAS. Files are named the way Jellyfin and Plex expect, and only your anime library gets scanned afterwards.

AniStash runs in its own Proxmox LXC, created with one command on the Proxmox host.

## Features

- **Browse and search**: trending and seasonal anime (posters and synopses from AniList), and ani-cli search with sub/dub.
- **Seasons**: a show's seasons are grouped automatically, including "Season 3 Part 2" and "Final Season". Queue every season at once, one season, or specific episodes (`1-5, 8, 12-`).
- **Follow shows**: tap *Follow* on a show and every new episode downloads by itself as it comes out, including new seasons. Checks are timed around each broadcast using AniList's airing schedule.
- **Highest quality with fallback**: each episode downloads at the best quality offered. If that download fails or comes out broken, AniStash tries the next quality down, then the next.
- **Files Jellyfin and Plex play directly**: every download is checked with ffprobe and remuxed (no re-encode) to a clean MP4 with faststart. Subtitles are converted to `.en.srt`. Layout: `Show (2013)/Season 01/Show (2013) - S01E01.mp4`.
- **Download locations**: add SMB or NFS shares, or local folders, from the UI. Each one is mounted, write-tested and remounted at boot. Pick one per download.
- **Jellyfin or Plex scans of the anime library only**: when a season finishes, AniStash scans just the new folders, or at most your chosen anime library. It never triggers a full scan.
- **Phone-friendly**: works on a phone and can be added to the home screen as an app.
- **Queue**: per-episode progress, cancel, retry and reorder. Episodes already on disk are skipped.
- **Updates**: `anistash-update`, or one button in the web UI. ani-cli and yt-dlp update themselves weekly.

## Install (Proxmox VE)

On the Proxmox host shell, as root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/JustinMarxGit/anistash/main/ct/anistash.sh)"
```

- **Default settings**: Debian, 2 cores, 1 GB RAM, 8 GB disk, DHCP on `vmbr0`. No questions asked.
- **Advanced settings**: container ID, hostname, password, Debian version, disk, CPU, RAM, storage, bridge, **static IPv4 + gateway**, IPv6, DNS, MAC, VLAN, MTU, SSH, tags (or none), web UI port and timezone. A summary screen shows everything before the container is created.

Every option can also be preset as an environment variable to skip its question. For example:

```bash
MODE=advanced CTID=120 NET=192.168.1.50/24 GATE=192.168.1.1 VLAN=20 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/JustinMarxGit/anistash/main/ct/anistash.sh)"
```

Variables: `MODE CTID HN PW OS_VERSION DISK CORES RAM STORAGE TEMPLATE_STORAGE BRG NET GATE IPV6 GATE6 DNS SD MAC VLAN MTU SSH SSH_KEY TAGS PORT TZ VERBOSE`. With `MODE` set, the script runs without any dialogs. `TAGS=` (empty) creates the container without any tags.

> The container is **privileged**, because an unprivileged LXC can't mount SMB/NFS shares itself. If you'd rather keep it unprivileged, mount the share on the Proxmox host, bind-mount it into the container, and add it in AniStash as a *local folder*.

Already have a Debian 12+ container? Run the installer inside it instead:

```bash
curl -fsSL https://raw.githubusercontent.com/JustinMarxGit/anistash/main/install/install.sh | bash
```

## Setup

The installer only sets up the container. Open `http://<container-ip>:8000` and the setup wizard walks you through:

1. A system check (ani-cli, yt-dlp, ffmpeg and a test search)
2. The admin account
3. Download locations: your NAS share (SMB or NFS) or a local folder, each mounted and tested
4. Jellyfin or Plex (optional): URL, API key or token, and the **anime library** to scan
5. Download defaults: sub/dub, maximum quality, downloads at once, naming

You can rerun it later from Settings → Setup. For the same steps in a terminal, run `anistash-setup` in the container's console.

### Jellyfin and Plex scans

AniStash never asks your media server to scan everything:

| Location has a "media server sees this as" path | AniStash scans |
| --- | --- |
| yes | only the new season folders (fastest) |
| no | only the anime library you picked |
| no library picked | nothing (the download shows a warning) |

The path is the same folder as Jellyfin or Plex sees it. For example, your NAS share `media/anime` is mounted in Jellyfin's container at `/media/anime`. The library dropdown shows each library's folders to help you fill it in.

- **Jellyfin API key:** Dashboard → API Keys → +
- **Plex token:** in Plex Web, open any item → ⋯ → Get Info → View XML, and copy the `X-Plex-Token` value from the address bar.

## Following shows

On a show page, **☆ Follow** keeps that show up to date:

- **Follow options:** pick the season to follow (the newest by default), whether to also follow new seasons, and what to do with episodes already out: download what's missing from every season, from just that season, or skip them and only get new ones.
- **Timing:** AniStash reads the airing schedule from AniList and checks about 20 minutes after each broadcast. Until the episode shows up on the site it checks every hour, because uploads trail the broadcast by a few hours and dubs by longer. Finished shows are still checked (every few hours, then daily, then weekly) for late dubs and new seasons.
- **What gets downloaded:** new episodes go through the normal queue, with the same quality fallback, naming and Jellyfin/Plex scan, tagged *auto*. Episodes already on disk are skipped. Failed ones are retried on later checks. Episodes you cancel are left alone.
- **Managing follows:** use **Downloads → Following** to see the next episode and when it airs, the last check, and to Check now, Pause, or Unfollow. Members can follow shows; viewers can't.

## Users and security

Settings → **Users** (admins only) is where you add accounts and give each person a role:

| Role | Can |
| --- | --- |
| **Admin** | everything: users, download locations, media server, settings, updates |
| **Member** | browse, search and download; cancel/retry/reorder **their own** downloads |
| **Viewer** | browse and search only |

New users get a temporary password (there's a *Generate* button) and must choose their own at first login. Admins can reset passwords, change roles, disable or delete accounts. There is always at least one active admin.

How it's protected:

- **Passwords** are hashed with scrypt (N=2^16, r=8, p=2, per-user salt), following OWASP's password storage guidance; they're never stored or logged in plain text. Passwords need 10+ characters and can't be common ones.
- **Sessions** use a random 256-bit token in an `HttpOnly`, `SameSite=Strict` cookie. Only a SHA-256 of it is stored. Sessions expire after 7 idle days / 30 days at most and end on logout, password change, role change or when an account is disabled. *Log out on all devices* is under Settings → Your account.
- **Brute force**: 5 failed logins lock that username, 20 lock that IP address, each for 15 minutes. Wrong usernames and wrong passwords get the same answer in the same time.
- **Cross-site requests** are rejected (custom request header + origin check), and every page sends a strict Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff` and `no-referrer`.
- **Access control** is enforced by the server on every request, not just hidden in the UI.
- **Secrets at rest**: the Jellyfin/Plex token is encrypted (Fernet: AES + HMAC) with a key in `/etc/anistash/secret.key` that only the service can read. NAS passwords live in root-only (`0600`) credential files, never in the database, because the kernel's SMB mount needs to read them. The database itself is readable only by the service.
- **Security log**: logins, failed attempts, lockouts and changes to users and settings, under Settings → Security log.

Locked out? In the container console, run `anistash-setup --reset-password`. It sets a new password and lifts the lockout.

For access from outside your home network, put AniStash behind a reverse proxy with HTTPS (the session cookie is marked `Secure` automatically when it sees HTTPS).

## Console commands (inside the LXC)

| Command | What it does |
| --- | --- |
| `anistash-setup` | Setup menu: account, locations, media server, defaults |
| `anistash-setup --reset-password` | New password for any user; also unlocks the account |
| `anistash-setup --reset` | Forget all configuration (downloads stay) |
| `anistash-update` | Update to the newest GitHub release (rolls back on failure) |
| `anistash-update --check` | Only check for an update |
| `anistash-update --version v0.1.0` | Install a specific release |
| `anistash-update --branch main` | Follow the development branch |
| `journalctl -u AniStash -f` | Live logs |

Settings, locations and the queue survive updates. The web UI port is set in `/etc/anistash/anistash.env`.

## Troubleshooting

- **Searches fail or return nothing**: the anime site probably changed. Click Settings → System → *Update ani-cli and yt-dlp*. This also runs weekly on its own.
- **A location shows "Not mounted"**: click *Test*, which remounts it and shows the error from the NAS. Check that the share allows the user you entered to write.
- **A download shows "scan failed"**: the file is downloaded fine; only the Jellyfin/Plex notification failed. Check the URL, key or token and the chosen library under Settings → Media server.

- **A dub download has Japanese audio**: AniStash asks ani-cli for the dub correctly (`--dub`), but with ani-cli 5.1.5 its current anime site sometimes hands back the sub stream for dubs. Run *Update ani-cli* under Settings → System; once ani-cli fixes it upstream, dubs come through without any change in AniStash.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
ANISTASH_DATA_DIR=./data .venv/bin/uvicorn app.main:app --reload
.venv/bin/python -m pytest
```

Locally, only *local folder* locations work, since SMB/NFS needs the root helper installed in the LXC. Tests use a fake `ani-cli` (`tests/fake_ani_cli.sh`) and need `ffmpeg`.

### How it talks to ani-cli

AniStash doesn't scrape anything itself; it drives the installed `ani-cli`:

- **Search results and episode lists:** ani-cli's menu program (`ANI_CLI_MENU`) is pointed at `app/menu_capture.sh`, which records the options and picks nothing.
- **Qualities:** read from ani-cli's `debug` player output.
- **Downloads:** the normal `-d -S -e -q` flags.

When the site changes, `ani-cli -U` fixes AniStash too.

### Layout

```
ct/anistash.sh          Proxmox host script (creates the LXC)
install/             in-container installer, deploy step, anistash-setup, anistash-update, systemd units
helper/anistash-helper  root helper (mounts, tool updates); the only thing the app may sudo
app/                 FastAPI backend and the static frontend (app/static)
tests/               pytest suite
```
