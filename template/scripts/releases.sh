#!/usr/bin/env bash
set -euo pipefail

# Release bookkeeping on the server, run by deploy.sh and rollback.sh from the
# project directory there, with COMPOSE set to the compose command.
#
#   releases.sh record <release>    tag the built images with the release,
#                                   remember it, forget the oldest ones
#   releases.sh restore [release]   run the images of a recorded release
#                                   again, the previous one by default
#   releases.sh list                recorded releases, oldest first
#
# Only the last RELEASES_KEPT releases (3) keep their images, a rollback goes
# back that far and no further. The list is .releases, one release a line.
# Deploys keep it in place.
#
# Which release runs is read from the images, the one whose tags point at the
# same images as `latest`, never from the list. A deploy that failed its health
# check leaves a build under `latest` that no release names, and a rollback
# from there goes back to the last recorded one.
#
# A rollback swaps images only. The compose files, configuration and settings
# on the server stay at the newest deploy, and migrations the newer release
# ran stay applied. It is safe while the older images run on all of those.

: "${COMPOSE:?COMPOSE is not set.}"
KEPT="${RELEASES_KEPT:-3}"
LIST=".releases"
IMAGES=""

die() { echo "$*" >&2; exit 1; }

[[ "${2:-}" =~ ^[A-Za-z0-9._-]*$ ]] || die "${2:-} is not a release."

# The images built for this project, one a line. Compose names them after the
# project directory and the service, which keeps the vendored infrastructure
# images out of it. A compose failure or no match at all stops the run,
# bookkeeping over nothing would report a release that was never kept.
images() {
    local names
    names=$($COMPOSE config --images)
    printf '%s\n' "$names" | sed 's/:.*$//' | grep "^${PWD##*/}-" | sort -u ||
        die "No image of ${PWD##*/} found in the compose files, is the project directory named after its compose project?"
}

identifier() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || true; }

# The last recorded release whose images are the ones under `latest`, empty
# when none is.
running() {
    local release image latest tagged found=""
    while read -r release; do
        for image in $IMAGES; do
            latest=$(identifier "$image:latest")
            tagged=$(identifier "$image:$release")
            if [ -z "$latest" ] || [ "$latest" != "$tagged" ]; then
                continue 2
            fi
        done
        found="$release"
    done < "$LIST"
    echo "$found"
}

record() {
    local release="${1:?record needs a release.}" image old kept
    for image in $IMAGES; do
        docker tag "$image:latest" "$image:$release"
    done

    # Deploying a release again moves it to the end instead of listing it twice.
    touch "$LIST"
    { grep -vxF -- "$release" "$LIST" || true; echo "$release"; } > "$LIST.next"
    kept=$(tail -n "$KEPT" "$LIST.next")
    for old in $(grep -vxF -- "$kept" "$LIST.next" || true); do
        for image in $IMAGES; do
            docker rmi "$image:$old" >/dev/null 2>&1 || true
        done
    done
    echo "$kept" > "$LIST"
    rm -f "$LIST.next"
}

restore() {
    local target="${1:-}" current image
    [ -s "$LIST" ] || die "No recorded releases."
    current=$(running)
    if [ -z "$target" ]; then
        if [ -z "$current" ]; then
            target=$(tail -n 1 "$LIST")
        else
            target=$(grep -B1 -xF -- "$current" "$LIST" | head -n 1)
        fi
    fi
    [ "$target" != "$current" ] || die "$current is the release that is running, there is no older one recorded."
    grep -qxF -- "$target" "$LIST" || die "$target is not a recorded release, the recorded ones are: $(tr '\n' ' ' < "$LIST")"

    for image in $IMAGES; do
        docker image inspect "$image:$target" >/dev/null 2>&1 || die "The image $image:$target is gone."
    done
    for image in $IMAGES; do
        docker tag "$image:$target" "$image:latest"
    done
    $COMPOSE up -d --no-build --remove-orphans --wait --wait-timeout 300
    echo "Restored release $target."
}

case "${1:-}" in
    record) IMAGES=$(images); record "${2:-}" ;;
    restore) IMAGES=$(images); restore "${2:-}" ;;
    list) cat "$LIST" 2>/dev/null || true ;;
    *) die "Usage: releases.sh record <release> | restore [release] | list" ;;
esac
