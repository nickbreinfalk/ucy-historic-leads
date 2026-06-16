# UCY Historic Leads

Slack bot + Supabase matcher: paste a ucymachines.com listing link → get a CSV
of historic Salesforce leads (companies that requested quotes for similar machines).

## Pieces
- Supabase Postgres: ~1M historic leads (company, contact, email, listing title, date)
- One-time normalization: messy listing titles → clean brand + category tags
- Slack bot: paste listing link → matched, deduped, ranked CSV reply

Secrets live in `.env` (gitignored). Lead data lives in `data/` (gitignored).
