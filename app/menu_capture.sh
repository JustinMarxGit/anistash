#!/bin/sh
# ANI_CLI_MENU shim: record the options ani-cli offers, select nothing.
# ani-cli calls "$menu_program" $extra_flags "$prompt" with options on stdin.
for last; do :; done
{
    printf '### %s\n' "$last"
    cat -
    printf '\n'
} >>"${ANIDL_CAPTURE:?ANIDL_CAPTURE not set}"
exit 1
