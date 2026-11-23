"""What's new in each release, in plain language. Shown once to each user after an update.

Newest first. Add an entry here for every release (with the VERSION bump).
"""

CHANGELOG = [
    {
        "version": "1.4.3",
        "title": "Splash!",
        "items": [
            "The light/dark drop is now a cartoon instead of slow motion: an outlined drop stretches as it falls, "
            "squashes as it lands and splashes, and bold rings roll out across the page and off the edges of the screen.",
        ],
    },
    {
        "version": "1.4.2",
        "title": "Slow motion",
        "items": [
            "The light/dark drop now falls in slow motion as a clear bead of water, its shadow gathering beneath it, "
            "and splashes. The new colours rush out and then slow to a gentle tail, while rings of light and shadow "
            "grow wider, softer and fainter as they cross the page.",
        ],
    },
    {
        "version": "1.4.1",
        "title": "A drop into still water",
        "items": [
            "The light/dark circle is now a drop: it falls onto the switch, and the new colours spread across the "
            "page like water, with light and dark rings rippling out behind.",
        ],
    },
    {
        "version": "1.4.0",
        "title": "Make it yours",
        "items": [
            "Settings → Appearance has a sandbox: try colours, pictures and styles in light and dark before you use "
            "them.",
            "Colours now come from all 348 combinations in Sanzo Wada's book, and they colour the background too, "
            "not just the buttons. Pick which colour does what, or use any colours you like.",
            "Upload your own background picture, logo and avatar, and choose fonts, text size, corners, spacing and "
            "poster size.",
            "Pick how light/dark switches: a circle growing from the button, a fade, or instant. There are new "
            "animations too: page transitions, tilting posters, drifting colour or falling petals, and a burst of "
            "petals when a download finishes.",
            "Share your look as a code. Admins can make theirs everyone's default, login page included.",
        ],
    },
    {
        "version": "1.3.0",
        "title": "Sign in with Jellyfin",
        "items": [
            "People on your Jellyfin server can sign in to AniStash with their Jellyfin username and password, once "
            "an admin enables them under Settings → Users → Jellyfin users.",
            "Someone who tries before being enabled shows up there as an access request.",
            "An AniStash account with the same name can be linked, so it switches to Jellyfin sign-in and keeps "
            "everything.",
            "People disabled in Jellyfin lose access here too, and their Picked for you uses their own Jellyfin "
            "watching automatically.",
        ],
    },
    {
        "version": "1.2.0",
        "title": "Colours from Sanzo Wada",
        "items": [
            "The colour presets under Settings → Appearance now come from Sanzo Wada's A Dictionary of Color "
            "Combinations (1933): Eosine Pink, Old Rose, Carmine, Yellow Orange, Cossack Green, Sea Green, "
            "Cerulian Blue, Olympic Blue, Deep Lyons Blue, Aconite Violet and Slate Color.",
            "Each swatch shows the two colours Wada paired it with; hover over it for the combination number.",
            "AniStash's own colour is his Red Orange. If you'd picked one of the old presets, your colour is kept "
            "(under Your own).",
        ],
    },
    {
        "version": "1.1.1",
        "title": "The logo wears your colour",
        "items": ["The AniStash logo at the top, on the login screen and in your browser tab now takes on the colour "
                  "you picked under Settings → Appearance."],
    },
    {
        "version": "1.1.0",
        "title": "Your colours, and a tidier Settings",
        "items": [
            "Switch between light and dark with the new button at the top, or let AniStash follow your device.",
            "Settings → Appearance: pick a colour scheme (Sakura, Matcha, Ocean, Violet and more) or any colour you "
            "like. It's yours alone and follows you to every device.",
            "Settings is now split into sections instead of one long page: a sidebar on a computer, a simple list "
            "you tap into on a phone.",
        ],
    },
    {
        "version": "1.0.0",
        "title": "AniStash 1.0",
        "items": [
            "AniStash is now a public 1.0 release: free and open source under the GPL-3.0 license, for anyone who "
            "wants an anime companion for Jellyfin or Plex.",
            "Nothing changes for you: your settings, users, follows and downloads carry over as they are.",
            "Final polish: clearer README with screenshots, backup and uninstall steps, a security policy, and "
            "automatic tests on every change.",
            "AniStash was made with Claude Code, Anthropic's AI coding assistant. The README explains how.",
        ],
    },
    {
        "version": "0.10.6",
        "title": "Cleaner spotlight on phones",
        "items": ["On phones the spotlight on Browse shows less on top of the artwork: a shorter details line, "
                  "compact buttons, a full-width timer line instead of arrows (swipe instead), and the picture "
                  "has more room."],
    },
    {
        "version": "0.10.5",
        "title": "Spotlight timer fixed",
        "items": ["After pressing the spotlight's next or back arrow, its timer line now always fills up again. "
                  "Hovering over a show still pauses it, so you can read."],
    },
    {
        "version": "0.10.4",
        "title": "Flick through the spotlight",
        "items": ["On phones, the big banner on Browse now follows your thumb: drag it and it lifts and tilts like a "
                  "card, with the next show waiting underneath. Flick it and it's thrown off the side; let go "
                  "gently and it springs back."],
    },
    {
        "version": "0.10.3",
        "title": "Rank numbers fixed",
        "items": ["Rank numbers 10 and up on Best this season and Trending are no longer cut off behind the "
                  "posters on phones."],
    },
    {
        "version": "0.10.2",
        "title": "A rotating spotlight",
        "items": [
            "The big banner at the top of Browse now cycles through the six most trending shows, a new one every "
            "7 seconds. Use the arrows or dots (or swipe on a phone) to move between them; hovering pauses it.",
            "Each one has More info (its anime page) and Find episodes.",
            "The genres on Discover are one scrolling row with arrows, like on Browse.",
        ],
    },
    {
        "version": "0.10.1",
        "title": "Scroll through genres",
        "items": ["The genre row on Browse has arrows to scroll through every genre, and fades at the edge that has "
                  "more to see."],
    },
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
