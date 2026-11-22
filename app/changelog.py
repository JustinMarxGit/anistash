"""What's new in each release, in plain language. Shown once to each user after an update.

Newest first. Add an entry here for every release (with the VERSION bump).
"""

CHANGELOG = [
    {
        "version": "0.10.0",
        "title": "Discover anime",
        "items": [
            "New Discover tab: browse every anime by genre (pick several to combine them), sort by popular, top rated, "
            "trending or newest, and narrow by type, status and year. It keeps loading as you scroll.",
            "Surprise me opens a random, well-liked anime, keeping to the genres you picked.",
            "Tap any poster for the anime's own page: what it's about, where it fits in its story, and More like this. "
            "Find episodes takes you on to downloading.",
            "Show pages also suggest More like this, and Browse has genre shortcuts.",
            "Settings now starts with updates (Check for updates is right at the top) and lists the most used settings first.",
        ],
    },
    {
        "version": "0.9.1",
        "title": "Picked for you learns from what you watch",
        "items": [
            "Picked for you now uses what you actually watch in Jellyfin or Plex: shows you watched a lot, recently "
            "or marked as favorite count most (\"Because you watched …\").",
            "Your Jellyfin/Plex account is found by your username. Change it under Settings → Your account.",
            "Windows like this one now keep the page behind them still until you close them.",
        ],
    },
    {
        "version": "0.9.0",
        "title": "Better recommendations",
        "items": [
            "Best this season ranks what's airing now by fan ratings from MyAnimeList, AniList and Kitsu, and by "
            "what people are watching on hianime. Hover over the number on a poster to see where it comes from.",
            "Picked for you suggests airing shows close to what you have and follow, and says why "
            "(\"Because you have Blue Box\", \"New season of …\").",
            "Posters show a Dub tag when an English dub is out.",
            "Admins can add a free MyAnimeList Client ID under Settings → Ratings for more reliable MAL scores.",
        ],
    },
    {
        "version": "0.8.1",
        "title": "Updating from the web page",
        "items": [
            "Update AniStash shows each step as it happens (download, install, restart), then reloads the page "
            "into the new version by itself. If an update fails, it tells you why.",
            "Every open AniStash page, on any device, reloads by itself after an update, so nobody keeps using the "
            "old version.",
            "Check for updates (Settings → System) looks for a new version right away.",
        ],
    },
    {
        "version": "0.8.0",
        "title": "Faster everywhere",
        "items": [
            "Show pages and search open much faster: lookups now run side by side instead of one after another, "
            "and answers are remembered across restarts.",
            "The Downloads page updates in place: no more flicker, and progress bars glide.",
            "Less work for your phone: refreshes pause while AniStash is in the background, pictures load as you "
            "scroll to them, and the app itself downloads at under a third of its old size.",
        ],
    },
    {
        "version": "0.7.0",
        "title": "A fresh new look",
        "items": [
            "AniStash has a new design: a seal logo, a featured show at the top of Browse, a trending top 10, "
            "show pages with big banner art, and smooth animations throughout.",
            "Switch titles between English and Japanese with EN / JA at the top of the page.",
            "Search finds a show by its English or Japanese name, whichever you type.",
        ],
    },
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
