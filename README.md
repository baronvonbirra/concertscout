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

## 📅 Automation & Deployment

- **Scouting**: The scouting script runs every Monday at 08:00 UTC via GitHub Actions (`scout_cron.yml`).
- **Web App**: The Streamlit interface is automatically deployed to **GitHub Pages** on every push to the `main` branch via GitHub Actions (`deploy.yml`).

### How the Deployment Works
The app is bundled into a static site using [stlite](https://github.com/whitphx/stlite), which allows Streamlit to run entirely in the browser using WebAssembly.
1. `generate_static.py` creates a `dist/` directory.
2. It generates an `index.html` that loads `stlite` and mounts `app.py`.
3. GitHub Actions deploys the `dist/` folder to your repository's GitHub Pages site.

### ⚠️ Security Note
GitHub Pages is a public hosting service. When using this deployment method:
- **Supabase credentials will be visible in the client-side code.**
- Ensure that you use the **"anon" key** with **Row Level Security (RLS)** properly configured on your Supabase database to restrict access.
