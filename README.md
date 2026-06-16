# UCY Historic Leads

Internal matching tool: given a machine listing, return a ranked shortlist of
prior enquirers who may be interested, delivered as a CSV into a private Slack channel.

## Pieces
- Supabase Postgres — private; RLS-enabled, accessed by a least-privilege app role.
- One-time normalization of listing titles → brand + machine-category tags (`normalize.py`).
- Tiered relevance matcher — brand / category / keyword (`match.py`, `core.py`).
- GitHub Actions cron poller (`poll.py`, `.github/workflows/poll.yml`) that posts the CSV
  to a private Slack channel a few minutes after a listing link is posted.
- `slackbot.py` — optional always-on Socket Mode variant for instant replies.

## Notes
- No credentials or data are in this repo. Secrets live in `.env`; bulk data in `data/`;
  both are gitignored.
- DB hardening / role setup is captured in `migrations/001_security.sql`.
- The runtime DB role can only read the match table and read/write the cursor table —
  no schema or destructive privileges.
