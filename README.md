# PUNK-SCOUT V2.0 🎸

Automated tracking of live music tours in Spain and Portugal for a curated artist list and discovery of related punk acts.

## 🤘 Features

- **Micro-Ingestion Engine**: Weekly automated micro-ingestion of curated Spotify playlists (and manual dispatch on-demand ingestion) to bypass unstable city scraping.
- **Enrichment Pipeline**: Matches concerts on Last.fm, resolves Instagram artist profiles via Spotify/DuckDuckGo Lite, and extracts tour keywords from recent social media posts.
- **Automatic Past Concert Sweeper (Self-Cleaning DB)**: Every time the scouting job runs, it automatically sweeps and deletes concerts where `event_date` is in the past, ensuring the database and frontend stay clean and clutter-free.
- **Manual "Force Refresh" Button**: Streamlit page-fetching uses caching (`ttl=3600`) for performance. A beautiful Neo-Brutalist "REFRESH" button on the UI lets users bypass this 1-hour cache instantly.
- **Neo-Brutalist Punk Aesthetic UI**: A high-contrast, DIY-aesthetic dashboard built with Alpine.js and Streamlit.
- **Deployments**: Static deployment to GitHub Pages using `@stlite/browser` (pinned to 1.4.0) with WebAssembly execution.

## 🛠 Technical Architecture

- **Backend**: Python 3.10+
- **Database**: Supabase (PostgreSQL) with Row Level Security (RLS) configured.
- **Frontend**: Streamlit + Alpine.js
- **APIs**: Spotify, Last.fm
- **CI/CD**: GitHub Actions

## 🔐 Security & Database Setup

### 1. Database Schema
Execute the SQL in `schema.sql` inside your Supabase SQL Editor to set up the `artists` and `concerts` tables.

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
