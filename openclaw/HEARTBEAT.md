# Heartbeat Checklist

On every heartbeat, briefly check:

1. Any open positions? If yes, run `dss position check` for stop/target status.
2. Any data freshness issues? Run `dss status` if last scan was >6h ago.
3. Any pending user questions in chat? Respond with data, not guesses.

Keep heartbeat runs minimal to save tokens. Full scans happen on cron schedule.
