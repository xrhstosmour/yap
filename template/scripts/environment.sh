#!/usr/bin/env bash
set -euo pipefail

# The server's settings, run by env.sh from the project directory there, with
# COMPOSE set to the compose command.
#
#   environment.sh list         the names that are set, never the values
#   environment.sh get KEY      one value
#   environment.sh set KEY      the value from standard input, then the stack
#                               is brought up again to pick it up
#
# The value is read from standard input so it never shows in a process list.
# deploy.sh writes this file again from .env.production, so a value set only
# here lasts until the next deploy.

: "${COMPOSE:?COMPOSE is not set.}"
FILE=".env"

die() { echo "$*" >&2; exit 1; }

[ -f "$FILE" ] || die "No $FILE here, deploy first."
[[ "${2:-A}" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "${2:-} is not a setting name."

case "${1:-}" in
    list)
        sed -n 's/^\([A-Z][A-Z0-9_]*\)=\(.\{0,1\}\).*$/\1 \2/p' "$FILE" |
            while read -r key first; do
                if [ -n "$first" ]; then echo "$key=<set>"; else echo "$key=<empty>"; fi
            done
        ;;
    get)
        [ -n "${2:-}" ] || die "get needs a setting name."
        grep "^$2=" "$FILE" | head -n 1 | cut -d= -f2- || die "$2 is not set."
        ;;
    set)
        [ -n "${2:-}" ] || die "set needs a setting name."
        IFS= read -r value || true
        cp -p "$FILE" "$FILE.previous"
        { grep -v "^$2=" "$FILE" || true; printf '%s=%s\n' "$2" "$value"; } > "$FILE.next"
        chmod 600 "$FILE.next"
        mv "$FILE.next" "$FILE"
        # A value the services cannot start on must not stay behind, the next
        # container recreated for any reason would fail on it.
        if ! $COMPOSE up -d --no-build --remove-orphans --wait --wait-timeout 300; then
            mv "$FILE.previous" "$FILE"
            echo "The stack did not come up with $2 set, the previous settings are back, bringing it up on them." >&2
            $COMPOSE up -d --no-build --remove-orphans --wait --wait-timeout 300
            exit 1
        fi
        rm -f "$FILE.previous"
        ;;
    *) die "Usage: environment.sh list | get KEY | set KEY" ;;
esac
