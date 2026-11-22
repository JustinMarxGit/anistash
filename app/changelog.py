"""What's new in each release, in plain language. Shown once to each user after an update.

Newest first. Add an entry here for every release (with the VERSION bump).
"""

CHANGELOG = [
    {
        "version": "0.6.0",
        "title": "Automatic updates, What's new and a guided tour",
        "items": [
            "Admins can let AniStash install updates by itself (Settings → System). It waits until nothing is "
            "downloading. With it off, admins see a banner when an update is available.",
            "After each update, this window shows what changed, once per person.",
            "New here? A short guided tour shows you around. Replay it any time under Settings → Your account.",
            "Finished downloads show when Jellyfin/Plex was asked to scan them, and which library.",
        ],
    },
    {
        "version": "0.5.0",
        "title": "Library badges, Sub + Dub and tooltips",
        "items": [
            "Shows you already have get a green ✓ In library badge on posters, search results and show pages.",
            "Sub + Dub downloads both versions into one file, so you can switch between Japanese and English in "
            "Jellyfin or Plex.",
            "Hover over any button (or press and hold it on a phone) to see what it does.",
        ],
    },
    {
        "version": "0.4.0",
        "title": "Follow shows",
        "items": [
            "Tap ☆ Follow on a show and new episodes download by themselves as they come out, new seasons too.",
            "Downloads now use the quality you pick.",
            "Scan now button when Jellyfin or Plex couldn't be told about new episodes.",
        ],
    },
    {
        "version": "0.3.0",
        "title": "AniStash, with user accounts",
        "items": [
            "anidl is now called AniStash.",
            "Add people under Settings → Users as admin, member (downloads) or viewer (browse only).",
            "Stronger security: protected passwords and sessions, lockout after repeated wrong passwords, and a security log.",
        ],
    },
    {
        "version": "0.2.2",
        "title": "Finds your anime folder",
        "items": ["Enter your NAS address and login, tap Connect, and AniStash finds the anime folder for you."],
    },
    {
        "version": "0.2.0",
        "title": "Easier download folders",
        "items": ["Paste one share address, or tap Find shares to pick it from a list."],
    },
]


def _parts(version: str) -> tuple:
    try:
        return tuple(int(x) for x in version.lstrip("v").split("."))
    except ValueError:
        return (0,)


def since(seen: str, current: str) -> list[dict]:
    """Entries newer than `seen`, up to `current`. Someone who never saw a changelog (an
    account from before this existed) gets just the newest entry."""
    upto = [e for e in CHANGELOG if _parts(e["version"]) <= _parts(current)]
    if not seen:
        return upto[:1]
    return [e for e in upto if _parts(e["version"]) > _parts(seen)]
