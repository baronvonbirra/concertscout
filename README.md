# PUNK-SCOUT V1.0 🎸

Automated tracking of live music tours in Spain for a curated artist list and discovery of related punk acts.

## 🤘 Features

- **Core Tracking**: Automatically monitors tour dates in Spain for your favorite core artists via Bandsintown API.
- **Discovery Engine**: Finds similar artists using Last.fm and validates them against a "Punk" genre filter (Punk, Hardcore, Crust, Post-Punk).
- **Zine-Style UI**: A high-contrast, DIY-aesthetic dashboard built with Streamlit.
- **Automation**: Weekly scouting runs via GitHub Actions.

## 🛠 Technical Architecture

- **Backend**: Python 3.10+
- **Database**: Supabase (PostgreSQL)
- **Frontend**: Streamlit
- **APIs**: Bandsintown, Last.fm
- **CI/CD**: GitHub Actions

## 🚀 Setup

### 1. Database Setup
Execute the SQL in `schema.sql` in your Supabase SQL Editor to create the `artists` and `events` tables.

### 2. Environment Variables
Create a `.env` file or set the following in your environment/secrets:
- `SUPABASE_URL`: Your Supabase project URL.
- `SUPABASE_KEY`: Your Supabase API key.
- `LASTFM_API_KEY`: Your Last.fm API key.
- `BANDSINTOWN_APP_ID`: Your Bandsintown App ID.

### 3. Installation
```bash
pip install -r requirements.txt
```

### 4. Running Locally
- **Scout for tours**: `python scout.py`
- **Launch UI**: `streamlit run app.py`

## 🧪 Testing
Run unit tests with mocks:
```bash
python test_scout.py
```

## 📅 Automation
The scouting script is configured to run every Monday at 08:00 UTC via GitHub Actions. See `.github/workflows/scout_cron.yml`.
