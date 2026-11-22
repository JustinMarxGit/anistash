#!/bin/sh
# Stand-in for ani-cli with fixed results. Talks to the menu shim the way ani-cli does:
# "$ANI_CLI_MENU" "$prompt" with "N title" options on stdin.
#   FAKE_VIDEO          a real short mp4 copied as the "download"
#   FAKE_FAIL="1080"    qualities whose download fails
#   FAKE_BROKEN="720"   qualities that produce a corrupt file
#   FAKE_DUB_VIDEO      mp4 used for --dub downloads (different audio)
#   FAKE_DUB_SAME=1     --dub returns the sub's video (what the site does today)
#   FAKE_NO_DUB=1       --dub downloads fail with "No sources found for dub"
download=0 index="" episode="" query="" quality=best dubbed=0
while [ $# -gt 0 ]; do
    case "$1" in
        -d) download=1 ;;
        -S) index="$2"; shift ;;
        -e) episode="$2"; shift ;;
        -q) quality="$2"; shift ;;
        --dub) dubbed=1 ;;
        -V) echo "9.9.9"; exit 0 ;;
        *) query="$query $1" ;;
    esac
    shift
done
query="${query# }"

case "$query" in
    nothing) printf 'No results found!\n'; exit 1 ;;
    blocked) printf 'Blocked by cloudflare.\n'; exit 1 ;;
    *) results="$(printf '1 Show\n2 Show Season 2\n3 Show: Final Season, Part 2')" ;;
esac

if [ -z "$index" ]; then
    if [ "$query" = single ]; then
        printf '1\n2\n' | "$ANI_CLI_MENU" "Select episode: " >/dev/null
    else
        printf '%s\n' "$results" | "$ANI_CLI_MENU" "Select anime: " >/dev/null
    fi
    printf 'Invalid anime selection\n'
    exit 1
fi

if [ "$download" = 1 ]; then
    [ "$episode" = 99 ] && { printf 'Invalid episode!\n'; exit 1; }
    for q in $FAKE_FAIL; do [ "$q" = "$quality" ] && { printf 'ERROR: fragment 3 not found\n'; exit 1; }; done
    printf '[download]  50.0%% of ~ 10.00MiB at 1.00MiB/s ETA 00:05\r[download] 100%% of 10.00MiB\n'
    out="$ANI_CLI_DOWNLOAD_DIR/Show Episode $episode.mp4"
    # A dub has different audio, unless the test wants the site's "same audio" behaviour
    if [ "$dubbed" = 1 ] && [ -n "$FAKE_DUB_VIDEO" ] && [ -z "$FAKE_DUB_SAME" ]; then cp "$FAKE_DUB_VIDEO" "$out"; else cp "$FAKE_VIDEO" "$out"; fi
    [ "$dubbed" = 1 ] && [ -n "$FAKE_NO_DUB" ] && { printf 'No sources found for dub!\n'; exit 1; }
    for q in $FAKE_BROKEN; do [ "$q" = "$quality" ] && printf 'not a video' >"$out"; done
    printf 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n' >"$ANI_CLI_DOWNLOAD_DIR/Show Episode $episode.vtt"
    exit 0
fi

if [ -n "$episode" ]; then
    # debug player output, as ani-cli prints it with ANI_CLI_PLAYER=debug
    printf 'All links:\n1080 >https://x/1080.m3u8\n720 >https://x/720.m3u8\n360 >https://x/360.m3u8\n'
    printf 'Selected link:\nhttps://x/1080.m3u8\nSubtitles:\n\n'
    exit 0
fi

printf '%s\n' ${FAKE_EPISODES:-1 2 3 12.5} | "$ANI_CLI_MENU" "Select episode: " >/dev/null
printf 'Invalid episode selection\n'
exit 1
