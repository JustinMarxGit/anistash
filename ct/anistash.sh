#!/usr/bin/env bash
# Creates an anistash LXC on a Proxmox VE host. Run on the host as root:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/JustinMarxGit/anistash/main/ct/anistash.sh)"
#
# Default settings ask nothing; Advanced settings walk through every option.
# Any option can be preset as an environment variable, which skips its question:
#
#   MODE=advanced|default  CTID  HN  PW  OS_VERSION  DISK  CORES  RAM  STORAGE  TEMPLATE_STORAGE
#   BRG  NET (dhcp or IP/CIDR)  GATE  IPV6 (auto|dhcp|none|IP/CIDR)  GATE6  DNS  SD  MAC  VLAN  MTU
#   SSH (yes|no)  SSH_KEY (path to a public key file)  TAGS (a;b, or empty for none)  PORT  TZ  VERBOSE (yes|no)
#
#   e.g.  CTID=120 NET=192.168.1.50/24 GATE=192.168.1.1 bash -c "$(curl -fsSL …/ct/anistash.sh)"
set -Eeuo pipefail

REPO="${ANISTASH_REPO:-JustinMarxGit/anistash}"
RAW="https://raw.githubusercontent.com/$REPO/main"

YW=$'\e[33m' GN=$'\e[1;92m' RD=$'\e[01;31m' BL=$'\e[36m' DGN=$'\e[32m' CL=$'\e[m' BOLD=$'\e[1m'
CM="${GN}✓${CL}" CROSS="${RD}✗${CL}" HOLD="-"
BACKTITLE="Proxmox VE · AniStash LXC"

header() {
    clear
    cat <<'EOF'
              _     _            _
   __ _ _ __ (_)___| |_ __ _ ___| |__
  / _` | '_ \| / __| __/ _` / __| '_ \
 | (_| | | | | \__ \ || (_| \__ \ | | |
  \__,_|_| |_|_|___/\__\__,_|___/_| |_|   anime downloads for your media server

EOF
}

spinner_pid=""
msg_info() {
    local msg="$1"
    printf " %s %s" "$HOLD" "${YW}${msg}${CL}"
    if [[ ${VERBOSE:-no} != yes && -t 1 ]]; then
        (while :; do for c in '⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏'; do printf "\r %s %s" "$c" "${YW}${msg}${CL}"; sleep 0.1; done; done) &
        spinner_pid=$!
    else
        echo
    fi
}
stop_spinner() { [[ -n $spinner_pid ]] && kill "$spinner_pid" 2>/dev/null && wait "$spinner_pid" 2>/dev/null; spinner_pid=""; true; }
msg_ok() { stop_spinner; printf "\r\e[K %s %s\n" "$CM" "${GN}$1${CL}"; }
msg_error() { stop_spinner; printf "\r\e[K %s %s\n" "$CROSS" "${RD}$1${CL}"; }
msg_warn() { stop_spinner; printf "\r\e[K %s %s\n" "${YW}!${CL}" "${YW}$1${CL}"; }

LOG="/tmp/anistash-lxc-$$.log"
CREATED=""
STEP="starting"
on_error() {
    local code=$? line=$1
    trap - ERR
    msg_error "Failed while: $STEP (line $line, exit $code)"
    echo -e "   Log: ${BL}$LOG${CL}"
    if [[ -n $CREATED ]] && whiptail --backtitle "$BACKTITLE" --title "Install failed" --yesno \
        "Creating AniStash failed while: $STEP.\n\nRemove the half-built container $CREATED?" 12 64; then
        pct stop "$CREATED" &>/dev/null || true
        pct destroy "$CREATED" --purge &>/dev/null || true
        echo -e " ${CM} Removed container $CREATED"
    fi
    exit "$code"
}
trap 'on_error $LINENO' ERR
on_interrupt() {
    stop_spinner
    echo
    if [[ -n $CREATED ]]; then
        echo -e " ${YW}Stopped.${CL} Container $CREATED was left in place. Either remove it:"
        echo "     pct stop $CREATED; pct destroy $CREATED --purge"
        echo "   or finish the install inside it (once its network works):"
        echo "     pct exec $CREATED -- bash -c \"curl -fsSL $RAW/install/install.sh | ANISTASH_PORT=${PORT:-8000} bash\""
    fi
    exit 130
}
trap on_interrupt INT
run() { if [[ ${VERBOSE:-no} == yes ]]; then "$@"; else "$@" >>"$LOG" 2>&1; fi; }

# Preflight --------------------------------------------------------------------

preflight() {
    [[ $EUID -eq 0 ]] || { msg_error "Run this script as root on the Proxmox host."; exit 1; }
    command -v pveversion >/dev/null || { msg_error "This isn't a Proxmox VE host (pveversion not found)."; exit 1; }
    [[ "$(dpkg --print-architecture)" == amd64 ]] || { msg_error "Only amd64 hosts are supported."; exit 1; }
    command -v whiptail >/dev/null || { msg_error "whiptail is missing: apt install whiptail"; exit 1; }
    curl -fsS --max-time 10 https://github.com >/dev/null || { msg_error "No internet access from the host (github.com unreachable)."; exit 1; }
}

# Settings --------------------------------------------------------------------

# Remembers which options came from the environment, so their dialogs are skipped
PRESET=" "
for v in CTID HN PW OS_VERSION DISK CORES RAM STORAGE TEMPLATE_STORAGE BRG NET GATE IPV6 GATE6 DNS SD MAC VLAN MTU SSH SSH_KEY TAGS PORT TZ VERBOSE; do
    [[ -n ${!v+x} ]] && PRESET+="$v "
done
preset() { [[ $PRESET == *" $1 "* ]]; }

storages() { pvesm status -content "$1" 2>/dev/null | awk 'NR>1 {print $1}'; }

defaults() {
    CTID="${CTID:-$(pvesh get /cluster/nextid)}"
    HN="${HN:-anistash}"
    PW="${PW:-}"
    OS_VERSION="${OS_VERSION:-newest}"
    DISK="${DISK:-8}"
    CORES="${CORES:-2}"
    RAM="${RAM:-1024}"
    local s
    s="$(storages rootdir)"
    STORAGE="${STORAGE:-$(grep -qx local-lvm <<<"$s" && echo local-lvm || head -n1 <<<"$s")}"
    s="$(storages vztmpl)"
    TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(grep -qx local <<<"$s" && echo local || head -n1 <<<"$s")}"
    BRG="${BRG:-vmbr0}"
    NET="${NET:-dhcp}"
    GATE="${GATE:-}"
    IPV6="${IPV6:-auto}"
    GATE6="${GATE6:-}"
    DNS="${DNS:-}"
    DNS_NOTE=""
    # "Same as host" copies the host's resolv.conf into the container. A Tailscale
    # address (100.64.0.0/10, e.g. MagicDNS or a tailnet Pi-hole) or a local stub
    # (127.x) only works on the host itself, so use public DNS instead.
    if [[ -z $DNS ]] && ! preset DNS; then
        local unreachable
        unreachable="$(host_only_dns)"
        if [[ -n $unreachable ]]; then
            DNS="1.1.1.1 8.8.8.8"
            DNS_NOTE="the host's DNS ($unreachable) only works on the host, e.g. Tailscale"
        fi
    fi
    SD="${SD:-}"
    MAC="${MAC:-}"
    VLAN="${VLAN:-}"
    MTU="${MTU:-}"
    SSH="${SSH:-no}"
    SSH_KEY="${SSH_KEY:-}"
    TAGS="${TAGS-anistash}"  # set but empty (TAGS=) means no tags
    PORT="${PORT:-8000}"
    TZ="${TZ:-$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo Etc/UTC)}"
    VERBOSE="${VERBOSE:-no}"
}

# Prints the host's nameservers that a container can't use (Tailscale CGNAT range or loopback)
host_only_dns() {
    awk '/^nameserver/ {print $2}' "${HOST_RESOLV_CONF:-/etc/resolv.conf}" 2>/dev/null |
        grep -E '^(127\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|::1$|fd7a:115c:a1e0:)' | paste -sd' ' || true
}

valid_ipv4_cidr() { [[ $1 =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]] && valid_octets "${1%/*}"; }
valid_ipv4() { [[ $1 =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && valid_octets "$1"; }
valid_octets() { local IFS=.; for o in $1; do ((o <= 255)) || return 1; done; }
id_taken() { pct status "$1" &>/dev/null || qm status "$1" &>/dev/null; }

# whiptail helpers: OK=0, Back (cancel)=1, Esc=255 → exit
ask() { # ask VAR "Title" "Prompt"
    local var=$1 out
    out="$(whiptail --backtitle "$BACKTITLE" --title "$2" --cancel-button Back --inputbox "$3" 10 70 "${!var}" 3>&1 1>&2 2>&3)" || return $?
    printf -v "$var" '%s' "$out"
}
choose() { # choose VAR "Title" "Prompt" tag desc ...
    local var=$1 title=$2 prompt=$3 out
    shift 3
    out="$(whiptail --backtitle "$BACKTITLE" --title "$title" --cancel-button Back --default-item "${!var}" \
        --menu "$prompt" 18 72 8 "$@" 3>&1 1>&2 2>&3)" || return $?
    printf -v "$var" '%s' "$out"
}
bad() { whiptail --backtitle "$BACKTITLE" --title "Invalid value" --msgbox "$1" 9 64; }

# Each step returns 0 (next), 1 (back) or 2 (ask again after an invalid value)
step_ctid() {
    preset CTID && return 0
    ask CTID "Container ID" "Container ID (must be unused):" || return 1
    [[ $CTID =~ ^[0-9]+$ ]] && ((CTID >= 100)) || { bad "The ID must be a number of 100 or more."; return 2; }
    ! id_taken "$CTID" || { bad "ID $CTID is already used by another container or VM."; return 2; }
}
step_hostname() {
    preset HN && return 0
    ask HN "Hostname" "Hostname:" || return 1
    [[ $HN =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || { bad "Use lowercase letters, digits and dashes."; return 2; }
}
step_password() {
    preset PW && return 0
    local a b
    a="$(whiptail --backtitle "$BACKTITLE" --title "Root password" --cancel-button Back --passwordbox \
        "Root password for the container.\nLeave blank for automatic console login (no password)." 11 70 3>&1 1>&2 2>&3)" || return 1
    if [[ -n $a ]]; then
        b="$(whiptail --backtitle "$BACKTITLE" --title "Root password" --passwordbox "Repeat the password:" 9 70 3>&1 1>&2 2>&3)" || return 2
        [[ $a == "$b" ]] || { bad "The passwords don't match."; return 2; }
        ((${#a} >= 5)) || { bad "Use at least 5 characters (a Proxmox requirement)."; return 2; }
    fi
    PW="$a"
}
step_os() {
    preset OS_VERSION && return 0
    choose OS_VERSION "Debian version" "Container OS:" newest "Newest Debian available (recommended)" 13 "Debian 13 (trixie)" 12 "Debian 12 (bookworm)" || return 1
}
step_resources() {
    preset DISK || { ask DISK "Disk size" "Disk size in GB (downloads go to your NAS, so this stays small):" || return 1; }
    [[ $DISK =~ ^[0-9]+$ ]] && ((DISK >= 4)) || { bad "Disk must be at least 4 GB."; return 2; }
    preset CORES || { ask CORES "CPU cores" "CPU cores:" || return 1; }
    [[ $CORES =~ ^[0-9]+$ ]] && ((CORES >= 1)) || { bad "Cores must be 1 or more."; return 2; }
    preset RAM || { ask RAM "Memory" "RAM in MB:" || return 1; }
    [[ $RAM =~ ^[0-9]+$ ]] && ((RAM >= 512)) || { bad "RAM must be at least 512 MB."; return 2; }
}
step_storage() {
    local items=() s
    if ! preset STORAGE; then
        for s in $(storages rootdir); do items+=("$s" ""); done
        ((${#items[@]} > 2)) && { choose STORAGE "Storage" "Storage for the container disk:" "${items[@]}" || return 1; }
    fi
    if ! preset TEMPLATE_STORAGE; then
        items=()
        for s in $(storages vztmpl); do items+=("$s" ""); done
        ((${#items[@]} > 2)) && { choose TEMPLATE_STORAGE "Template storage" "Storage for the Debian template:" "${items[@]}" || return 1; }
    fi
    [[ -n $STORAGE && -n $TEMPLATE_STORAGE ]] || { bad "No storage found for containers or templates."; return 1; }
}
step_bridge() {
    preset BRG && return 0
    local items=() b
    for b in $(ip -o link show type bridge | awk -F': ' '{print $2}' | sed 's/@.*//' | sort); do items+=("$b" ""); done
    ((${#items[@]})) || items=(vmbr0 "")
    choose BRG "Network bridge" "Bridge:" "${items[@]}" || return 1
}
step_ipv4() {
    if ! preset NET; then
        local mode
        mode="$( [[ $NET == dhcp ]] && echo dhcp || echo static)"
        choose mode "IPv4" "How does the container get its IPv4 address?" dhcp "DHCP (automatic)" static "Static address" || return 1
        if [[ $mode == static ]]; then
            [[ $NET == dhcp ]] && NET=""
            ask NET "Static IPv4" "Address with prefix, e.g. 192.168.1.50/24:" || return 1
            valid_ipv4_cidr "$NET" || { bad "\"$NET\" isn't an address like 192.168.1.50/24."; NET=dhcp; return 2; }
        else
            NET=dhcp
        fi
    fi
    if [[ $NET != dhcp ]]; then
        if ! preset GATE; then
            [[ -n $GATE ]] || GATE="$(sed -E 's#^([0-9]+\.[0-9]+\.[0-9]+)\.[0-9]+/.*#\1.1#' <<<"$NET")"
            ask GATE "Gateway" "IPv4 gateway:" || return 1
        fi
        valid_ipv4 "$GATE" || { bad "\"$GATE\" isn't a valid gateway address."; return 2; }
    else
        GATE=""
    fi
}
step_ipv6() {
    preset IPV6 || { choose IPV6 "IPv6" "IPv6:" auto "SLAAC (automatic)" dhcp "DHCPv6" none "No IPv6" static "Static address" || return 1; }
    if [[ $IPV6 == static ]]; then
        IPV6=""
        ask IPV6 "Static IPv6" "IPv6 address with prefix, e.g. fd00::50/64:" || return 1
        ask GATE6 "IPv6 gateway" "IPv6 gateway (optional):" || return 1
    fi
    [[ $IPV6 =~ ^(auto|dhcp|none)$ || $IPV6 =~ ^[0-9a-fA-F:]+/[0-9]{1,3}$ ]] || { bad "\"$IPV6\" isn't a valid IPv6 setting."; IPV6=auto; return 2; }
}
step_dns() {
    local prompt="DNS servers, separated by spaces (blank = same as the Proxmox host):"
    [[ -n $DNS_NOTE ]] && prompt+="\n\nPre-filled because $DNS_NOTE."
    preset DNS || { ask DNS "DNS server" "$prompt" || return 1; }
    local ns
    for ns in $DNS; do
        valid_ipv4 "$ns" || [[ $ns =~ ^[0-9a-fA-F:]+$ ]] || { bad "\"$ns\" isn't an IP address."; return 2; }
    done
    if [[ -z $DNS && -n "$(host_only_dns)" ]]; then
        bad "The host's DNS ($(host_only_dns)) only works on the host (Tailscale or a local resolver), so the container couldn't resolve names. Enter a DNS server, e.g. your router or 1.1.1.1."
        return 2
    fi
    preset SD || { ask SD "DNS search domain" "DNS search domain (blank = same as the host):" || return 1; }
}
step_link() {
    preset MAC || { ask MAC "MAC address" "MAC address (blank = random):" || return 1; }
    [[ -z $MAC || $MAC =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || { bad "Use the form AA:BB:CC:DD:EE:FF."; return 2; }
    preset VLAN || { ask VLAN "VLAN" "VLAN tag (blank = none):" || return 1; }
    [[ -z $VLAN ]] || { [[ $VLAN =~ ^[0-9]+$ ]] && ((VLAN >= 1 && VLAN <= 4094)); } || { bad "VLAN must be 1-4094."; return 2; }
    preset MTU || { ask MTU "MTU" "MTU (blank = default):" || return 1; }
    [[ -z $MTU ]] || { [[ $MTU =~ ^[0-9]+$ ]] && ((MTU >= 576 && MTU <= 65520)); } || { bad "MTU must be 576-65520."; return 2; }
}
step_ssh() {
    preset SSH || { choose SSH "SSH" "Allow root login over SSH?" no "No (use the Proxmox console)" yes "Yes" || return 1; }
    if [[ $SSH == yes ]] && ! preset SSH_KEY; then
        ask SSH_KEY "SSH key" "Path to a public key file on this host to authorize (optional):" || return 1
        [[ -z $SSH_KEY || -f $SSH_KEY ]] || { bad "$SSH_KEY doesn't exist."; return 2; }
    fi
    [[ $SSH == yes && -z $PW && -z $SSH_KEY ]] && { bad "SSH needs a root password or a key. Set a password or add a key."; return 2; }
    return 0
}
step_app() {
    preset PORT || { ask PORT "Web UI port" "Port for the anistash web UI:" || return 1; }
    [[ $PORT =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || { bad "Port must be 1-65535."; return 2; }
    preset TZ || { ask TZ "Timezone" "Timezone (e.g. Europe/London):" || return 1; }
    [[ -f /usr/share/zoneinfo/$TZ ]] || { bad "Unknown timezone \"$TZ\"."; return 2; }
}
step_tags() {
    preset TAGS && return 0
    local out
    out="$(whiptail --backtitle "$BACKTITLE" --title "Container tags" --cancel-button Back --inputbox \
        "Proxmox tags for this container, separated by semicolons.\nLeave empty for no tags." 11 70 "$TAGS" 3>&1 1>&2 2>&3)" || return 1
    out="$(tr -d '[:space:]' <<<"$out" | tr ',' ';' | sed 's/;;*/;/g; s/^;//; s/;$//')"
    local tag_pattern='^[A-Za-z0-9_;-]*$'
    [[ $out =~ $tag_pattern ]] || { bad "Tags can only contain letters, numbers, - and _ (separate them with ;)."; return 2; }
    TAGS="$out"
}
step_verbose() {
    preset VERBOSE && return 0
    choose VERBOSE "Verbose mode" "Show full apt/pip output while installing?" no "No, show progress only" yes "Yes" || return 1
}

advanced() {
    local steps=(step_ctid step_hostname step_password step_os step_resources step_storage step_bridge
        step_ipv4 step_ipv6 step_dns step_link step_ssh step_tags step_app step_verbose)
    local i=0 rc
    while ((i < ${#steps[@]})); do
        # Called in a || list so a "back" (non-zero) result doesn't trip the ERR trap
        rc=0
        "${steps[$i]}" || rc=$?
        case $rc in
            0) i=$((i + 1)) ;;
            1) ((i == 0)) && return 1; i=$((i - 1)) ;;
            2) ;;            # invalid value: ask the same step again
            *) exit 0 ;;     # Esc
        esac
    done
}

summary() {
    local net6="$IPV6"
    [[ -n $GATE6 ]] && net6+=" via $GATE6"
    cat <<EOF
Container ID:   $CTID
Hostname:       $HN
Root password:  $([[ -n $PW ]] && echo set || echo "none (console autologin)")
OS:             Debian $OS_VERSION
Disk / CPU / RAM: ${DISK} GB / $CORES cores / $RAM MB
Storage:        $STORAGE (templates: $TEMPLATE_STORAGE)
Bridge:         $BRG${VLAN:+ (VLAN $VLAN)}${MTU:+, MTU $MTU}
IPv4:           $NET${GATE:+ via $GATE}
IPv6:           $net6
DNS:            ${DNS:-host}${SD:+, search $SD}${DNS_NOTE:+ (because $DNS_NOTE)}
MAC:            ${MAC:-random}
SSH root login: $SSH
Tags:           ${TAGS:-none}
Web UI port:    $PORT
Timezone:       $TZ
Type:           privileged (needed to mount SMB/NFS shares from the web UI)
EOF
}

# Container creation --------------------------------------------------------------

pick_template() {
    STEP="downloading the Debian template"
    msg_info "Updating the template list"
    run pveam update
    local want="$OS_VERSION"
    [[ $want == newest ]] && want="1[2-9]"
    TEMPLATE="$(pveam available --section system | awk '{print $2}' | grep -E "^debian-${want}(\.[0-9]+)*-standard_.*_amd64\.tar\.(zst|gz|xz)$" | sort -V | tail -n1 || true)"
    [[ -n $TEMPLATE ]] || { msg_error "No Debian $OS_VERSION template found in pveam."; exit 1; }
    msg_ok "Template ${BL}$TEMPLATE${GN}"
    if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
        msg_info "Downloading $TEMPLATE"
        run pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
        msg_ok "Downloaded the template"
    fi
}

create_container() {
    STEP="creating the container"
    local net="name=eth0,bridge=$BRG,ip=$NET"
    [[ -n $GATE ]] && net+=",gw=$GATE"
    case "$IPV6" in
        none) ;;
        auto | dhcp) net+=",ip6=$IPV6" ;;
        *) net+=",ip6=$IPV6"; [[ -n $GATE6 ]] && net+=",gw6=$GATE6" ;;
    esac
    [[ -n $MAC ]] && net+=",hwaddr=$MAC"
    [[ -n $VLAN ]] && net+=",tag=$VLAN"
    [[ -n $MTU ]] && net+=",mtu=$MTU"

    local args=(
        --hostname "$HN" --cores "$CORES" --memory "$RAM" --swap 512
        --rootfs "$STORAGE:$DISK" --net0 "$net" --ostype debian
        --unprivileged 0 --features "nesting=1,mount=nfs;cifs" --onboot 1
        --timezone "$TZ"
    )
    # Only pass tags when there are any (an empty TAGS means no tags at all)
    [[ -n $TAGS ]] && args+=(--tags "$TAGS")
    [[ -n $PW ]] && args+=(--password "$PW")
    [[ -n $DNS ]] && args+=(--nameserver "$DNS")
    [[ -n $SD ]] && args+=(--searchdomain "$SD")
    [[ -n $SSH_KEY ]] && args+=(--ssh-public-keys "$SSH_KEY")

    msg_info "Creating LXC container $CTID"
    # pct extracts the template under the caller's umask. A hardened host umask (027)
    # leaves /etc at 750, and then apt (which resolves names as the _apt user) can't
    # read resolv.conf. The Proxmox GUI uses 022, so do the same.
    local prev_umask
    prev_umask="$(umask)"
    umask 022
    run pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" "${args[@]}"
    umask "$prev_umask"
    CREATED="$CTID"
    msg_ok "Created LXC container ${BL}$CTID${GN} ($HN)"

    STEP="starting the container"
    msg_info "Starting the container"
    run pct start "$CTID" || [[ "$(pct status "$CTID" 2>/dev/null)" == "status: running" ]]
    local i
    for i in {1..10}; do
        [[ "$(pct status "$CTID" 2>/dev/null)" == "status: running" ]] && break
        sleep 1
    done
    [[ "$(pct status "$CTID" 2>/dev/null)" == "status: running" ]] ||
        { msg_error "Container $CTID was created but won't start. See: pct start $CTID --debug"; false; }
    msg_ok "Started the container"

    wait_for_ip
    check_connectivity
    fix_container_os
    check_dns
}

# Runs a command in the container with a time limit, so nothing can hang the script
cexec() { timeout "$1" pct exec "$CTID" -- "${@:2}"; }

# Same approach as the community scripts: poll for an address (IPv4, else IPv6),
# quickly at first and then less often, for up to about two minutes.
wait_for_ip() {
    STEP="waiting for an IP address"
    msg_info "Waiting for the container network"
    local i
    IP=""
    for i in {1..60}; do
        IP="$(cexec 10 ip -4 addr show dev eth0 2>/dev/null | awk '/inet / {print $2}' | cut -d/ -f1 | head -n1 || true)"
        [[ -n $IP ]] || IP="$(cexec 10 ip -6 addr show dev eth0 scope global 2>/dev/null | awk '/inet6 / {print $2}' | cut -d/ -f1 | head -n1 || true)"
        [[ -n $IP ]] && break
        if ((i <= 20)); then sleep 1; elif ((i <= 40)); then sleep 2; else sleep 3; fi
    done
    if [[ -n $IP ]]; then
        msg_ok "Network connected: ${BL}$IP"
        return 0
    fi

    msg_error "No IP address assigned to container $CTID"
    local uplinks=0 port link
    link="$(cexec 10 ip -br link show dev eth0 2>/dev/null | awk '{print $2}' || true)"
    echo "   Container network: ip=$NET${VLAN:+, VLAN $VLAN}, eth0 ${link:-missing}"
    if [[ ! -d /sys/class/net/$BRG ]]; then
        echo "   Bridge $BRG doesn't exist on this host."
    else
        for port in /sys/class/net/"$BRG"/brif/*; do
            [[ -e $port ]] || continue
            case "${port##*/}" in veth* | tap* | fwpr* | fwln*) ;; *) uplinks=$((uplinks + 1)) ;; esac
        done
        ((uplinks)) || echo "   Bridge $BRG has no uplink port, so nothing outside this host can answer on it."
    fi
    [[ $NET == dhcp ]] && echo "   No DHCP server answered on $BRG${VLAN:+ (VLAN $VLAN)}. Try a static IP under Advanced settings."
    echo "   Also check the Proxmox firewall for this container."
    false
}

# Ping first because it's fast; fall back to a TCP connect, since many networks drop ping.
# Like the community scripts, a failure here is only a warning: DNS and apt decide.
check_connectivity() {
    STEP="checking internet access"
    for _ in 1 2 3; do
        if cexec 10 ping -c 1 -W 2 1.1.1.1 &>/dev/null || cexec 10 ping -c 1 -W 2 8.8.8.8 &>/dev/null ||
            cexec 10 bash -c 'exec 3<>/dev/tcp/1.1.1.1/443' &>/dev/null || cexec 10 bash -c 'exec 3<>/dev/tcp/8.8.8.8/443' &>/dev/null; then
            msg_ok "Internet reachable"
            return 0
        fi
        sleep 2
    done
    msg_warn "IP $IP is set but the internet connectivity test failed. Continuing; check gateway ${GATE:-from DHCP} if the install fails."
}

# Fixes the community scripts apply to every Debian container before installing.
fix_container_os() {
    STEP="preparing the container OS"
    # Debian 13 templates can leave / owned by nobody
    cexec 20 bash -c '[ "$(stat -c %U /)" = root ] || chown root:root /' >>"$LOG" 2>&1 || true
    # /etc without o+x stops apt's _apt user from reading resolv.conf
    cexec 20 bash -c '[ $(( 0$(stat -c %a /etc) & 1 )) -eq 1 ] || chmod o+x /etc' >>"$LOG" 2>&1 || true
    # Nothing in anistash needs the boot to wait for "network online"
    cexec 20 systemctl disable -q --now systemd-networkd-wait-online.service >>"$LOG" 2>&1 || true
}

dns_works() {
    cexec 30 getent hosts deb.debian.org &>/dev/null && cexec 30 getent hosts github.com &>/dev/null
}

# If the configured resolver doesn't answer, switch to public resolvers (what the
# community scripts do) and keep them on the container so they survive reboots.
check_dns() {
    STEP="checking DNS"
    msg_info "Checking DNS"
    if dns_works; then
        msg_ok "DNS works"
        return 0
    fi
    local current
    current="$(cexec 10 awk '/^nameserver/ {print $2}' /etc/resolv.conf 2>/dev/null | paste -sd' ' || true)"
    msg_warn "DNS lookups fail with ${current:-no nameserver}; switching the container to 8.8.8.8 and 1.1.1.1"
    cexec 10 bash -c "printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' >/etc/resolv.conf" >>"$LOG" 2>&1 || true
    pct set "$CTID" --nameserver "8.8.8.8 1.1.1.1" >>"$LOG" 2>&1 || true
    if dns_works; then
        msg_ok "DNS works with public resolvers"
        return 0
    fi
    msg_error "DNS still fails inside the container, even with public resolvers."
    if cexec 20 getent ahostsv4 github.com &>/dev/null; then
        echo "   IPv4 (A) lookups work but IPv6 (AAAA) lookups time out; your router or firewall is dropping AAAA queries."
    else
        echo "   Something between the container and the internet is blocking DNS (UDP/TCP port 53)."
        echo "   Check the Proxmox firewall, your router's firewall, or DNS filtering such as Pi-hole or AdGuard."
    fi
    echo "   Test from the host: pct exec $CTID -- getent hosts github.com"
    false
}

configure_access() {
    STEP="setting up console and SSH access"
    msg_info "Setting up access"
    if [[ -z $PW ]]; then
        # No password: log in automatically on the Proxmox console
        pct exec "$CTID" -- bash -c 'mkdir -p /etc/systemd/system/container-getty@1.service.d && cat >/etc/systemd/system/container-getty@1.service.d/override.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear --keep-baud tty%I 115200,38400,9600 \$TERM
EOF
systemctl daemon-reload; systemctl restart container-getty@1.service' >>"$LOG" 2>&1
    fi
    if [[ $SSH == yes ]]; then
        local permit=prohibit-password
        [[ -n $PW ]] && permit=yes
        pct exec "$CTID" -- bash -c "sed -i -E 's/^#?PermitRootLogin.*/PermitRootLogin $permit/' /etc/ssh/sshd_config && systemctl enable --now ssh" >>"$LOG" 2>&1
    else
        pct exec "$CTID" -- bash -c "systemctl disable --now ssh ssh.socket" >>"$LOG" 2>&1 || true
    fi
    msg_ok "Console autologin: $([[ -z $PW ]] && echo on || echo off), SSH: $SSH"
}

install_app() {
    STEP="installing anistash inside the container"
    msg_info "Installing AniStash (a few minutes)"
    run pct exec "$CTID" -- bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates"
    local verbose=0
    [[ $VERBOSE == yes ]] && verbose=1
    # Fetched on the host and handed over, like the community scripts do
    local installer
    installer="$(curl -fsSL "$RAW/install/install.sh")"
    run pct exec "$CTID" -- env ANISTASH_PORT="$PORT" ANISTASH_REPO="$REPO" TZ="$TZ" ANISTASH_VERBOSE="$verbose" \
        bash -c "$installer"
    msg_ok "Installed AniStash"

    STEP="writing the container notes"
    IP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
    pct set "$CTID" --description "$(cat <<EOF
# AniStash

**Web UI:** [http://$IP:$PORT](http://$IP:$PORT)

- Finish or redo setup: open the web UI, or run \`anistash-setup\` in this console
- Update anistash: \`anistash-update\` (or Settings → System in the web UI)
- Locked out: \`anistash-setup --reset-password\`
- Logs: \`journalctl -u anistash -f\`

Source: https://github.com/$REPO
EOF
)" >>"$LOG" 2>&1
}

main() {
    header
    preflight
    defaults

    local mode="${MODE:-}"
    while :; do
        if [[ -z $mode ]]; then
            mode="$(whiptail --backtitle "$BACKTITLE" --title "AniStash LXC" --menu \
                "Create a Debian LXC running anistash.\n\nDefault settings: $CORES cores, $RAM MB RAM, ${DISK} GB disk, DHCP on $BRG." \
                15 70 3 default "Default settings" advanced "Advanced settings (static IP, VLAN, storage…)" exit "Exit" \
                3>&1 1>&2 2>&3)" || exit 0
        fi
        case "$mode" in
            exit) exit 0 ;;
            advanced) advanced || { mode=""; continue; } ;;
            default)
                preset CTID || ! id_taken "$CTID" || CTID="$(pvesh get /cluster/nextid)" ;;
            *) msg_error "MODE must be default or advanced"; exit 1 ;;
        esac
        # Scripted runs (MODE set) skip the confirmation
        if [[ -n ${MODE:-} ]] || whiptail --backtitle "$BACKTITLE" --title "Create this container?" \
            --yes-button "Create" --no-button "Go back" --yesno "$(summary)" 24 78; then
            break
        fi
        mode=""
    done

    header
    echo -e "${BOLD}Creating AniStash LXC${CL}\n"
    summary | sed 's/^/   /'
    echo
    pick_template
    create_container
    configure_access
    install_app
    trap - ERR

    echo
    echo -e " ${CM} ${GN}Done!${CL} AniStash is running in container ${BL}$CTID${CL}."
    echo -e "   Open ${BL}http://$IP:$PORT${CL} to finish setup (admin account, download locations, Jellyfin/Plex)."
    echo -e "   ${DGN}Console tools inside the container: anistash-setup, anistash-update${CL}"
    rm -f "$LOG"
}

main "$@"
