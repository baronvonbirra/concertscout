# PUNK-SCOUT V2.0 🎸

Automated tracking of live music tours in Spain and Portugal for a curated artist list and discovery of related punk acts.

## 🤘 Features

- **Micro-Ingestion Engine**: Weekly automated micro-ingestion of curated Spotify playlists (and manual dispatch on-demand ingestion) to bypass unstable city scraping.
- **Enrichment Pipeline**: Matches concerts on Last.fm, resolves Instagram artist profiles via Spotify/DuckDuckGo Lite, and extracts tour keywords from recent social media posts.
- **Automatic Past Concert Sweeper (Self-Cleaning DB)**: Every time the scouting job runs, it automatically sweeps and deletes concerts where `event_date` is in the past, ensuring the database and frontend stay clean and clutter-free.
- **Manual "Force Refresh" Button**: Streamlit page-fetching uses caching (`ttl=3600`) for performance. A beautiful Neo-Brutalist "REFRESH" button on the UI lets users bypass this 1-hour cache instantly.
- **Neo-Brutalist Punk Aesthetic UI**: A high-contrast, DIY-aesthetic dashboard built with Alpine.js and Streamlit.
- **Deployments**: Static deployment to GitHub Pages using `@stlite/browser` (pinned to 1.4.0) with WebAssembly execution.

## 📅 Scheduled Cron Jobs (GitHub Actions)

All backend automation workflows are verified and configured as automated GitHub Actions cron jobs:

1. **Monday Playlist Curation (`scout_monday_playlist.yml`)**
   - **Cron Schedule**: `0 7 * * 1` (Mondays at 07:00 UTC)
   - **Command**: `python scout.py --monday-playlist`
   - **Description**: Selects exactly 10 fresh tracks meeting strict tier distribution & frequency cap rules for 'Punk in Progress', prunes tracks older than 84 days, and syncs discovered acts to the Tour Tracker DB.

2. **Wednesday Weekly Ingestion & Tour Scouting (`scout_cron.yml`)**
   - **Cron Schedule**: `0 7 * * 3` (Wednesdays at 07:00 UTC)
   - **Command**: `python scout.py --weekly`
   - **Description**: Parses Weekly Punk playlist positions 2–7, resolves Instagram profiles, and scrapes Last.fm for upcoming tour dates in Spain and Portugal.

3. **Sunday Band Analytics Snapshot (`scout_sunday_analytics.yml`)**
   - **Cron Schedule**: `0 22 * * 0` (Sundays at 22:00 UTC)
   - **Command**: `python scout.py --analytics`
   - **Description**: Captures weekly listener/follower snapshots across tracked bands, calculates WoW/MoM growth percentages, updates momentum scores (0–100), and re-evaluates growth trajectories.

4. **On-Demand Playlist Ingestion (`scout_manual.yml`)**
   - **Trigger**: `workflow_dispatch` (Manual)
   - **Command**: `python scout.py --playlist "<url>"`
   - **Description**: Ingests custom Spotify playlist URLs on-demand into the artist registry.

5. **Static Site Deployment (`deploy.yml`)**
   - **Trigger**: `push` to `main` branch / `workflow_dispatch`
   - **Command**: `python generate_static.py`
   - **Description**: Regenerates the WebAssembly stlite static bundle and deploys to GitHub Pages.

## 🛠 Technical Architecture

- **Backend**: Python 3.10+
- **Database**: Supabase (PostgreSQL) with Row Level Security (RLS) configured.
- **Frontend**: Streamlit + Alpine.js
- **APIs**: Spotify, Last.fm
- **CI/CD**: GitHub Actions

## 🔐 Security & Database Setup

### 1. Database Schema
Execute the SQL in `schema.sql` inside your Supabase SQL Editor to set up the database tables (including `weekly_submissions`, `artists`, `concerts`, `tour_events`, `band_registry`, etc.).

If updating an existing database instance where `share_recommendation` column is missing from `weekly_submissions`, execute `add_share_recommendation_column.sql` in the Supabase SQL Editor and reload the PostgREST schema cache:
```sql
NOTIFY pgrst, 'reload schema';
```

### 2. Row Level Security (RLS) & API Keys
Because GitHub Pages is a public hosting service, the `SUPABASE_KEY` exposed to the frontend should be your project's **public anon key**.

#### Permissive (Default) Setup
To allow the public `anon` key to write directly to the database during scraper runs (if using the same key everywhere), run `schema.sql` which enables RLS and explicitly grants `ALL` (Select, Insert, Update, Delete) privileges to the `anon` role.

#### Production Hardened Setup (Highly Recommended)
For maximum security:
1. **GitHub Secrets**: Set `SUPABASE_KEY` in your GitHub repository secrets to your Supabase **service_role key** (not the public `anon` key).
2. **Database Policies**: Restrict your database RLS policies so that the `anon` role is strictly read-only:
   ```sql
   -- Only allow read access to the public anon role
   DROP POLICY IF EXISTS "Allow anon insert access" ON artists;
   DROP POLICY IF EXISTS "Allow anon update access" ON artists;
   DROP POLICY IF EXISTS "Allow anon delete access" ON artists;
   DROP POLICY IF EXISTS "Allow anon insert access" ON concerts;
   DROP POLICY IF EXISTS "Allow anon update access" ON concerts;
   DROP POLICY IF EXISTS "Allow anon delete access" ON concerts;
   ```
The `service_role` key bypasses RLS and handles all inserts/deletes securely in the background, while the public frontend can only read.

## 🚀 Local Run

### 1. Environment Variables
Create a `.env` file with the following credentials:
- `SUPABASE_URL`: Your Supabase project URL.
- `SUPABASE_KEY`: Your Supabase API key (use `service_role` key for full backend permissions).
- `LASTFM_API_KEY`: Your Last.fm API key.
- `SPOTIFY_CLIENT_ID`: Your Spotify Client ID.
- `SPOTIFY_CLIENT_SECRET`: Your Spotify Client Secret.

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Running Jobs Locally
- **Run Weekly Ingestion/Enrichment & Sweeper**: `python scout.py --weekly`
- **On-Demand Playlist Ingestion**: `python scout.py --playlist "SPOTIFY_PLAYLIST_URL"`
- **Start UI Frontend**: `streamlit run app.py`

## 🧪 Testing
Run unit tests to verify the backend behavior:
```bash
python test_scout.py
```
