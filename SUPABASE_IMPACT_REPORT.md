# Supabase Data API Root Endpoint Removal - Impact Assessment

## Summary
The project **concertscout** is **NOT impacted** by the upcoming removal of anon key access to the Supabase Data API root endpoint (`/rest/v1/`) scheduled for April 8th, 2026.

## Findings
- **Official Client Usage**: The project exclusively uses the official `supabase-py` client library (version 2.4.5) to interact with the database.
- **Table-Level Targeting**: All database queries in `app.py` and `scout.py` are performed using the `.table("table_name")` method (e.g., `supabase.table("artists")`, `supabase.table("events")`).
- **No Root Endpoint Calls**: A thorough audit of the codebase confirmed that there are no manual HTTP requests (via `requests`, `httpx`, or other libraries) targeting the root `/rest/v1/` endpoint.
- **Safe Usage Pattern**: According to the Supabase notification, "Normal Data API usage, i.e. querying tables via /rest/v1/your_table or via any Supabase client library is not affected." Our implementation follows this exact pattern.

## Verification
- Code audit completed for `app.py`, `scout.py`, and `generate_static.py`.
- Test suite (`test_scout.py`) executed successfully, confirming that the current client-based interaction model is functional and stable.

## Security Hardening (Addressing Linter Errors)
Although the core functionality remains unaffected by the Data API root removal, the project has been updated to address identified security linter errors regarding Row Level Security (RLS).

- **RLS Enabled**: Row Level Security is now explicitly enabled in `schema.sql` for all tables (`artists`, `events`, `locations`, `similar_artists_cache`, `keywords`).
- **Public Read Policies**: `SELECT` policies have been established for the `anon` (public) role to ensure the frontend can continue to fetch and display concert data.
- **Service Role Policies**: Full access policies for the `service_role` have been added to allow the backend scraper to operate correctly within an authenticated context.

## Conclusion
The project is secure and no additional changes are required to maintain functionality after April 8th, 2026.
