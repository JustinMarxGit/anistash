# anidl

A self-hosted web UI for [ani-cli](https://github.com/pystardust/ani-cli). Browse and search anime from your phone or desktop, pick whole series, seasons or single episodes, and download them to your NAS. Files are named the way Jellyfin and Plex expect, and only your anime library gets scanned afterwards.

anidl runs in its own Proxmox LXC, created with one command on the Proxmox host.

## Features

- **Browse and search**: trending and seasonal anime (posters and synopses from AniList), and ani-cli search with sub/dub.
- **Seasons**: a show's seasons are grouped automatically, including "Season 3 Part 2" and "Final Season". Queue every season at once, one season, or specific episodes (`1-5, 8, 12-`).
- **Highest quality with fallback**: each episode downloads at the best quality offered. If that download fails or comes out broken, anidl tries the next quality down, then the next.
- **Files Jellyfin and Plex play directly**: every download is checked with ffprobe and remuxed (no re-encode) to a clean MP4 with faststart. Subtitles are converted to `.en.srt`. Layout: `Show (2013)/Season 01/Show (2013) - S01E01.mp4`.
- **Download locations**: add SMB or NFS shares, or local folders, from the UI. Each one is mounted, write-tested and remounted at boot. Pick one per download.
- **Jellyfin or Plex scans of the anime library only**: when a season finishes, anidl scans just the new folders, or at most your chosen anime library. It never triggers a full scan.
- **Phone-friendly**: works on a phone and can be added to the home screen as an app.
- **Queue**: per-episode progress, cancel, retry and reorder. Episodes already on disk are skipped.
- **Updates**: `anidl-update`, or one button in the web UI. ani-cli and yt-dlp update themselves weekly.

## Install (Proxmox VE)

On the Proxmox host shell, as root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/JustinMarxGit/anidl/main/ct/anidl.sh)"
```

- **Default settings**: Debian, 2 cores, 1 GB RAM, 8 GB disk, DHCP on `vmbr0`. No questions asked.
- **Advanced settings**: container ID, hostname, password, Debian version, disk, CPU, RAM, storage, bridge, **static IPv4 + gateway**, IPv6, DNS, MAC, VLAN, MTU, SSH, tags (or none), web UI port and timezone. A summary screen shows everything before the container is created.

Every option can also be preset as an environment variable to skip its question. For example:

```bash
MODE=advanced CTID=120 NET=192.168.1.50/24 GATE=192.168.1.1 VLAN=20 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/JustinMarxGit/anidl/main/ct/anidl.sh)"
```

Variables: `MODE CTID HN PW OS_VERSION DISK CORES RAM STORAGE TEMPLATE_STORAGE BRG NET GATE IPV6 GATE6 DNS SD MAC VLAN MTU SSH SSH_KEY TAGS PORT TZ VERBOSE`. With `MODE` set, the script runs without any dialogs. `TAGS=` (empty) creates the container without any tags.

> The container is **privileged**, because an unprivileged LXC can't mount SMB/NFS shares itself. If you'd rather keep it unprivileged, mount the share on the Proxmox host, bind-mount it into the container, and add it in anidl as a *local folder*.

Already have a Debian 12+ container? Run the installer inside it instead:

```bash
curl -fsSL https://raw.githubusercontent.com/JustinMarxGit/anidl/main/install/install.sh | bash
```

## Setup

The installer only sets up the container. Open `http://<container-ip>:8000` and the setup wizard walks you through:

1. A system check (ani-cli, yt-dlp, ffmpeg and a test search)
2. The admin account
3. Download locations: your NAS share (SMB or NFS) or a local folder, each mounted and tested
4. Jellyfin or Plex (optional): URL, API key or token, and the **anime library** to scan
5. Download defaults: sub/dub, maximum quality, downloads at once, naming

You can rerun it later from Settings → Setup. For the same steps in a terminal, run `anidl-setup` in the container's console.

### Jellyfin and Plex scans

anidl never asks your media server to scan everything:

| Location has a "media server sees this as" path | anidl scans |
| --- | --- |
| yes | only the new season folders (fastest) |
| no | only the anime library you picked |
| no library picked | nothing (the download shows a warning) |

The path is the same folder as Jellyfin or Plex sees it. For example, your NAS share `media/anime` is mounted in Jellyfin's container at `/media/anime`. The library dropdown shows each library's folders to help you fill it in.

- **Jellyfin API key:** Dashboard → API Keys → +
- **Plex token:** in Plex Web, open any item → ⋯ → Get Info → View XML, and copy the `X-Plex-Token` value from the address bar.

## Console commands (inside the LXC)

| Command | What it does |
| --- | --- |
| `anidl-setup` | Setup menu: account, locations, media server, defaults |
| `anidl-setup --reset-password` | New admin password when you're locked out |
| `anidl-setup --reset` | Forget all configuration (downloads stay) |
| `anidl-update` | Update to the newest GitHub release (rolls back on failure) |
| `anidl-update --check` | Only check for an update |
| `anidl-update --version v0.1.0` | Install a specific release |
| `anidl-update --branch main` | Follow the development branch |
| `journalctl -u anidl -f` | Live logs |

Settings, locations and the queue survive updates. The web UI port is set in `/etc/anidl/anidl.env`.

## Troubleshooting

- **Searches fail or return nothing**: the anime site probably changed. Click Settings → System → *Update ani-cli and yt-dlp*. This also runs weekly on its own.
- **A location shows "Not mounted"**: click *Test*, which remounts it and shows the error from the NAS. Check that the share allows the user you entered to write.
- **A download shows "scan failed"**: the file is downloaded fine; only the Jellyfin/Plex notification failed. Check the URL, key or token and the chosen library under Settings → Media server.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
ANIDL_DATA_DIR=./data .venv/bin/uvicorn app.main:app --reload
.venv/bin/python -m pytest
```

Locally, only *local folder* locations work, since SMB/NFS needs the root helper installed in the LXC. Tests use a fake `ani-cli` (`tests/fake_ani_cli.sh`) and need `ffmpeg`.

### How it talks to ani-cli

anidl doesn't scrape anything itself; it drives the installed `ani-cli`:

- **Search results and episode lists:** ani-cli's menu program (`ANI_CLI_MENU`) is pointed at `app/menu_capture.sh`, which records the options and picks nothing.
- **Qualities:** read from ani-cli's `debug` player output.
- **Downloads:** the normal `-d -S -e -q` flags.

When the site changes, `ani-cli -U` fixes anidl too.

### Layout

```
ct/anidl.sh          Proxmox host script (creates the LXC)
install/             in-container installer, deploy step, anidl-setup, anidl-update, systemd units
helper/anidl-helper  root helper (mounts, tool updates); the only thing the app may sudo
app/                 FastAPI backend and the static frontend (app/static)
tests/               pytest suite
```
