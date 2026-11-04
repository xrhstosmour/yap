#!/usr/bin/env bash

# Sourced by deploy.sh, rollback.sh, logs.sh and env.sh, never run on its own.
# Gives them the same messages and the same way of reaching the server.

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# The stack as the server runs it, from inside the project directory there.
# shellcheck disable=SC2034 # Used by the scripts that source this file.
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.app.yml"

# set_value <key> <value> <file>, replaces the line of a settings file.
set_value() {
    local key="$1" value="$2" file="$3"
    grep -v "^${key}=" "$file" > "$file.next" || true
    printf '%s=%s\n' "$key" "$value" >> "$file.next"
    # Settings hold secrets, a rewrite must not leave them readable by others.
    chmod 600 "$file.next"
    mv "$file.next" "$file"
}

# connect <user@host> <port> <identity file or empty>
# Checks the values that end up in commands for the server, then defines
# SSH_OPTIONS and remote().
connect() {
    TARGET="$1"
    SSH_PORT="$2"
    IDENTITY="$3"

    [[ "$TARGET" =~ ^[a-z_][a-z0-9_-]*@[A-Za-z0-9.:-]+$ ]] || error "$TARGET is not user@host."
    [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || error "$SSH_PORT is not a port."

    SSH_OPTIONS=(-p "$SSH_PORT" -o ConnectTimeout=15)
    if [ -n "$IDENTITY" ]; then
        SSH_OPTIONS+=(-i "$IDENTITY" -o IdentitiesOnly=yes)
    fi
}

# Commands for the server are built by the caller, from values checked above.
# shellcheck disable=SC2029
remote() { ssh "${SSH_OPTIONS[@]}" "$TARGET" "$@"; }
