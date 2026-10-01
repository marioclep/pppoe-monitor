def compute_delta(
    current_uptime: int,
    current_bytes: int,
    last_uptime: int | None,
    last_bytes: int | None,
) -> int:
    """Compute bytes transferred since the last poll for a single counter
    (rx or tx), handling PPPoE session restarts.

    Mikrotik resets the interface byte counter to 0 whenever a PPPoE
    session reconnects (a new dynamic interface is created). We detect
    that by uptime going backwards relative to the last poll.
    """
    if last_uptime is None or last_bytes is None:
        return current_bytes
    if current_uptime < last_uptime:
        return current_bytes
    return max(current_bytes - last_bytes, 0)
