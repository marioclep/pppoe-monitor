#!/bin/sh
# Daily pg_dump of the monitor database, with rotation.
#
#   (no argument)  loop forever: dump every day at BACKUP_TIME (HH:MM, local TZ)
#   now            dump once right away and exit (use before upgrading)
#
# Connection comes from PGHOST/PGUSER/PGPASSWORD/PGDATABASE. Dumps are written
# as <name>.partial and renamed only when pg_dump succeeds, so a file named
# pppoe-*.dump is always complete.
set -u

BACKUP_DIR=/backups
BACKUP_TIME="${BACKUP_TIME:-03:00}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"
}

run_backup() {
    target="$BACKUP_DIR/pppoe-$(date +%Y%m%d-%H%M%S).dump"
    # Unique partial name (mktemp, not $$: every container has its own PID
    # namespace): a manual "now" at the same moment as the scheduled run must
    # not write into the same file.
    partial=$(mktemp "$target.partial.XXXXXX") || {
        log "ERROR could not create a file in $BACKUP_DIR"
        return 1
    }
    chmod 644 "$partial"  # mktemp creates 0600; the host user must be able to copy dumps
    [ -e "$target" ] && target="${target%.dump}-${partial##*.}.dump"
    log "starting $target"
    if ! pg_dump -Fc -f "$partial"; then
        rm -f "$partial"
        log "ERROR pg_dump failed, no backup written"
        return 1
    fi
    if ! mv -n "$partial" "$target" || [ -e "$partial" ]; then
        rm -f "$partial"
        log "ERROR could not rename $partial to $target"
        return 1
    fi
    log "done $target ($(du -h "$target" | cut -f1))"
    find "$BACKUP_DIR" -maxdepth 1 -name 'pppoe-*.dump' -mtime +"$BACKUP_KEEP_DAYS" -print -delete |
        while read -r removed; do log "removed old backup $removed"; done
    find "$BACKUP_DIR" -maxdepth 1 -name 'pppoe-*.dump.partial*' -mtime +0 -delete
}

seconds_until_next_run() {
    now=$(date +%s)
    next=$(date -d "today $BACKUP_TIME" +%s 2>/dev/null) || return 1
    if [ "$next" -le "$now" ]; then
        next=$(date -d "tomorrow $BACKUP_TIME" +%s) || return 1
    fi
    echo $((next - now))
}

case "${1:-}" in
    now)
        run_backup
        exit $?
        ;;
    "")
        ;;
    *)
        echo "usage: backup.sh [now]" >&2
        exit 2
        ;;
esac

case "$BACKUP_TIME" in
    [0-2][0-9]:[0-5][0-9]) ;;
    *) log "ERROR invalid BACKUP_TIME=$BACKUP_TIME (expected HH:MM)"; exit 1 ;;
esac

log "daily backup at $BACKUP_TIME (TZ=${TZ:-UTC}), keeping $BACKUP_KEEP_DAYS days in $BACKUP_DIR"
while true; do
    wait_seconds=$(seconds_until_next_run) || {
        log "ERROR invalid BACKUP_TIME=$BACKUP_TIME (expected HH:MM)"
        exit 1
    }
    log "next backup in ${wait_seconds}s"
    sleep "$wait_seconds"
    run_backup || true
done
