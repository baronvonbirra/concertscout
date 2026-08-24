import os
import requests
from supabase import create_client, Client
from dotenv import load_dotenv
import time
import random
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import json
import argparse
import sys
import re
import base64
import urllib.parse

load_dotenv()

_monthly_listeners_cache = {}

# Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY")
BANDSINTOWN_API_KEY = os.environ.get("BANDSINTOWN_API_KEY")
SONGKICK_API_KEY = os.environ.get("SONGKICK_API_KEY")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    print("Warning: Supabase credentials not fully set.")
if not BANDSINTOWN_API_KEY:
    print("Warning: BANDSINTOWN_API_KEY not set in environment. Bandsintown API calls will fall back to default identifier.")
if not all([SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET]):
    print("Warning: Spotify Client ID or Secret not set. Ingestion will be skipped.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
]

BOT_USER_AGENTS = [
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Twitterbot/1.0",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
]

RATE_LIMIT_DELAY = 0.5

# Circuit breaker flags and failure counters to prevent hammering when blocked
LASTFM_BLOCKED = False
DDG_BLOCKED = False
_DOMAIN_FAILURES = {"last.fm": 0, "duckduckgo.com": 0}
_MAX_CONSECUTIVE_FAILURES = 3

# Spotify Auth Cache
_spotify_token_cache = {"token": None, "expires_at": 0}

def get_spotify_write_token():
    refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN")
    if not refresh_token:
        print("Warning: SPOTIFY_REFRESH_TOKEN not found in environment. Falling back to client credentials (this token cannot modify playlists)...")
        return get_spotify_token()

    now = time.time()
    try:
        url = "https://accounts.spotify.com/api/token"
        headers = {}
        if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
            auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
            auth_b64 = base64.b64encode(auth_str.encode()).decode()
            headers["Authorization"] = f"Basic {auth_b64}"
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            }
        else:
            print("Error: Spotify Client ID or Secret not set. Cannot use refresh token flow. Falling back to client credentials (this token cannot modify playlists)...")
            return get_spotify_token()

        res = requests.post(url, data=data, headers=headers, timeout=10)
        if res.status_code != 200:
            print(f"Error: Spotify token refresh failed with status code {res.status_code}. Response body: {res.text}")
        res.raise_for_status()
        res_data = res.json()
        return res_data.get("access_token")
    except Exception as e:
        print(f"Error refreshing Spotify user token: {e}. Falling back to client credentials (this token cannot modify playlists)...")
        return get_spotify_token()

def get_monthly_listeners(artist_id):
    if not artist_id:
        return 0
    if artist_id in _monthly_listeners_cache:
        return _monthly_listeners_cache[artist_id]

    url = f"https://open.spotify.com/artist/{artist_id}"

    # Scout V2.0 rule: mandatory 0.5-second delay before all external API calls
    time.sleep(0.5)

    # Try bot User-Agents first as Spotify returns OG description with monthly listeners for social crawlers
    for ua in BOT_USER_AGENTS:
        headers = {
            "User-Agent": ua,
            "Accept-Language": "en-US,en;q=0.9"
        }
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                meta = soup.find("meta", property="og:description")
                if not meta:
                    meta = soup.find("meta", attrs={"name": "description"})
                if meta:
                    content = meta.get("content", "")
                    match = re.search(r"([\d.,MKB]+)\s+monthly\s+listeners", content, re.IGNORECASE)
                    if match:
                        listeners_str = match.group(1).replace(",", "")
                        if "M" in listeners_str.upper():
                            val = float(listeners_str.upper().replace("M", "")) * 1_000_000
                        elif "K" in listeners_str.upper():
                            val = float(listeners_str.upper().replace("K", "")) * 1_000
                        elif "B" in listeners_str.upper():
                            val = float(listeners_str.upper().replace("B", "")) * 1_000_000_000
                        else:
                            val = float(listeners_str)
                        count = int(val)
                        _monthly_listeners_cache[artist_id] = count
                        return count
        except Exception as e:
            print(f"Error scraping monthly listeners for artist {artist_id}: {e}")

    # Fallback mechanism: fetch the artist's followers count from the official Spotify Web API
    token = get_spotify_token()
    if token:
        try:
            print(f"Spotify monthly listeners scraping returned 0 for artist {artist_id}. Falling back to fetching followers count from Spotify Web API...")
            api_url = f"https://api.spotify.com/v1/artists/{artist_id}"
            api_headers = {"Authorization": f"Bearer {token}"}
            # Scout V2.0 rule: mandatory 0.5-second delay before all external API calls
            time.sleep(0.5)
            api_res = requests.get(api_url, headers=api_headers, timeout=10)
            if api_res.status_code == 200:
                artist_data = api_res.json()
                followers = artist_data.get("followers", {}).get("total", 0)
                if followers > 0:
                    _monthly_listeners_cache[artist_id] = followers
                    return followers
        except Exception as e:
            print(f"Error fetching Spotify followers fallback for artist {artist_id}: {e}")

    _monthly_listeners_cache[artist_id] = 0
    return 0

def discover_punk_candidates(token, window_days=7, existing_candidates=None):
    print(f"--- Starting Candidate Discovery and Classification (Release window: past {window_days} days) ---")
    headers = {"Authorization": f"Bearer {token}"}
    if existing_candidates is None:
        candidates = {}
    elif isinstance(existing_candidates, list):
        candidates = {c["track_id"]: dict(c) for c in existing_candidates}
    else:
        candidates = {k: dict(v) for k, v in existing_candidates.items()}

    execution_date = datetime.now().date()
    effective_start_date = execution_date - timedelta(days=window_days)

    def parse_release_date(date_str):
        if not date_str:
            return None
        try:
            if len(date_str) == 4:
                return datetime.strptime(date_str, "%Y").date()
            elif len(date_str) == 7:
                return datetime.strptime(date_str, "%Y-%m").date()
            else:
                return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    def is_eligible(track_name, album_name, release_date_str, effective_start_date):
        rel_date = parse_release_date(release_date_str)
        if not rel_date or rel_date < effective_start_date or rel_date > execution_date:
            return False

        excluded_keywords = ["remix", "compilation", "live", "acoustic", "demo", "instrumental", "remaster", "mix"]
        text = f"{track_name} {album_name}".lower()
        for kw in excluded_keywords:
            if re.search(rf"\b{kw}\b", text):
                return False
        return True

    def process_track_item(track, effective_start_date):
        if not track:
            return
        track_id = track.get("id")
        if not track_id or track_id in candidates:
            return

        track_name = track.get("name", "")
        album = track.get("album", {})
        album_name = album.get("name", "")
        release_date_str = album.get("release_date")

        if is_eligible(track_name, album_name, release_date_str, effective_start_date):
            artists = track.get("artists", [])
            if artists:
                primary_artist = artists[0]
                artist_id = primary_artist.get("id")
                artist_name = primary_artist.get("name")

                if artist_id:
                    candidates[track_id] = {
                        "track_id": track_id,
                        "track_name": track_name,
                        "album_name": album_name,
                        "release_date": release_date_str,
                        "artist_id": artist_id,
                        "artist_name": artist_name,
                        "spotify_id": artist_id
                    }

    genres = [
        "punk", "punk rock", "pop punk", "hardcore", "skate punk",
        "post-punk", "melodic hardcore", "hardcore punk", "garage punk", "emo", "ska", "celtic punk"
    ]

    print(f"Searching Spotify candidate tracks (Release window: past {window_days} days, start date: {effective_start_date})...")

    # 1. Search across expanded punk genres with pagination & tag/year queries
    for genre in genres:
        query_templates = [
            f'genre:"{genre}"',
            f'genre:"{genre}" tag:new',
            f'genre:"{genre}" year:{execution_date.year}'
        ]
        for query in query_templates:
            for offset in [0, 50]:
                url = "https://api.spotify.com/v1/search"
                params = {
                    "q": query,
                    "type": "track",
                    "market": "ES",
                    "limit": 50,
                    "offset": offset
                }
                time.sleep(0.5)
                try:
                    res = requests.get(url, headers=headers, params=params, timeout=10)
                    if res.status_code == 200:
                        tracks = res.json().get("tracks", {}).get("items", [])
                        for track in tracks:
                            process_track_item(track, effective_start_date)
                except Exception as e:
                    print(f"Error performing search for '{query}' offset {offset}: {e}")

    # 2. Query Spotify Browse New Releases API
    for offset in [0, 50]:
        browse_url = "https://api.spotify.com/v1/browse/new-releases"
        browse_params = {"country": "ES", "limit": 50, "offset": offset}
        time.sleep(0.5)
        try:
            b_res = requests.get(browse_url, headers=headers, params=browse_params, timeout=10)
            if b_res.status_code == 200:
                albums = b_res.json().get("albums", {}).get("items", [])
                for album in albums:
                    album_id = album.get("id")
                    album_name = album.get("name", "")
                    release_date_str = album.get("release_date")
                    if album_id and parse_release_date(release_date_str) and parse_release_date(release_date_str) >= effective_start_date:
                        t_url = f"https://api.spotify.com/v1/albums/{album_id}/tracks"
                        time.sleep(0.5)
                        t_res = requests.get(t_url, headers=headers, params={"limit": 10}, timeout=10)
                        if t_res.status_code == 200:
                            a_tracks = t_res.json().get("items", [])
                            for tr in a_tracks:
                                tr["album"] = {"name": album_name, "release_date": release_date_str}
                                process_track_item(tr, effective_start_date)
        except Exception as e:
            print(f"Error fetching browse new releases offset {offset}: {e}")

    print(f"Discovered {len(candidates)} candidates matching past {window_days}-day release window.")

    classified_candidates = []

    for t_id, item in candidates.items():
        if "tier" not in item:
            artist_id = item["artist_id"]
            listeners = get_monthly_listeners(artist_id)

            if listeners > 100000:
                tier = "Major"
            elif listeners >= 10000:
                tier = "Mid"
            elif listeners >= 1000:
                tier = "Indie"
            else:
                tier = "Emerging"

            item["monthly_listeners"] = listeners
            item["tier"] = tier

        classified_candidates.append(item)

    return classified_candidates

def sample_unique_artists(candidates_list, count, unavailable_artists):
    """
    Filters candidates_list to exclude any candidate whose artist_id is in unavailable_artists.
    Groups the remaining candidates by artist_id, randomly samples up to `count` unique artists,
    selects a random track for each chosen artist, and adds those artists to unavailable_artists.
    """
    eligible = [c for c in candidates_list if c["artist_id"] not in unavailable_artists]

    by_artist = {}
    for c in eligible:
        aid = c["artist_id"]
        if aid not in by_artist:
            by_artist[aid] = []
        by_artist[aid].append(c)

    artists_available = list(by_artist.keys())
    selected_tracks = []

    if len(artists_available) >= count:
        selected_artists = random.sample(artists_available, count)
    else:
        selected_artists = artists_available

    for aid in selected_artists:
        track = random.choice(by_artist[aid])
        selected_tracks.append(track)
        unavailable_artists.add(aid)

    return selected_tracks

def select_weekly_playlist_tracks(candidates, existing_playlist_artists=None, current_week=None):
    if not current_week:
        current_week = f"W{datetime.now().isocalendar()[1]}"
    print(f"--- Selecting Tracks for Weekly Discovery Playlist ({current_week} Phase 2 Spec) ---")
    if not candidates:
        print("Warning: No candidates found.")
        return []

    # Get band registry data and top50 set from Supabase
    band_registry_map = {}
    top50_bands = set()

    if supabase:
        try:
            res = supabase.table("band_registry").select("*").execute()
            if res.data:
                for row in res.data:
                    band_registry_map[row["band_name"].lower()] = row
                    if row.get("ever_featured_in_top50"):
                        top50_bands.add(row["band_name"].lower())
        except Exception as e:
            print(f"Error querying band_registry: {e}")

    # Helper to parse week string (e.g., "W33" -> 33)
    def parse_week_num(w_str):
        if not w_str or not isinstance(w_str, str):
            return 0
        match = re.search(r"\d+", w_str)
        return int(match.group(0)) if match else 0

    curr_w_num = parse_week_num(current_week)

    def is_band_excluded(band_name, max_excluded_weeks=10):
        b_lower = band_name.lower()
        if b_lower not in band_registry_map:
            return False
        last_used = band_registry_map[b_lower].get("last_used_in_playlist")
        if not last_used:
            return False
        last_w_num = parse_week_num(last_used)
        if curr_w_num > 0 and last_w_num > 0:
            if curr_w_num - last_w_num <= max_excluded_weeks:
                return True
        return False

    # Sort candidates: (listener_count ASC) -> (release_date DESC) -> random tie-break
    sorted_candidates = list(candidates)
    random.shuffle(sorted_candidates)  # tie-break
    sorted_candidates.sort(key=lambda c: (c.get("monthly_listeners", 0), c.get("release_date", "")))

    active_playlist_artists = set(existing_playlist_artists) if existing_playlist_artists else set()

    # Multi-stage relaxation ladder for exclusion window: 10 weeks -> 6 weeks -> 4 weeks -> 2 weeks -> 0 weeks (no recency cap)
    exclusion_tiers = [10, 6, 4, 2, 0]
    selected_tracks = []
    seen_bands = set()
    total_unique_candidate_bands = len({c["artist_name"].lower() for c in sorted_candidates})

    for max_weeks in exclusion_tiers:
        for c in sorted_candidates:
            if len(selected_tracks) >= 10:
                break
            b_name = c["artist_name"]
            b_lower = b_name.lower()
            a_id = c.get("artist_id")

            if a_id and a_id in active_playlist_artists:
                continue
            if b_lower in seen_bands:
                continue
            if is_band_excluded(b_name, max_excluded_weeks=max_weeks):
                continue

            seen_bands.add(b_lower)
            selected_tracks.append(c)

        if len(selected_tracks) >= 10 or total_unique_candidate_bands < 10:
            break
        print(f"Relaxing band registry recency exclusion to {max_weeks} weeks (currently selected {len(selected_tracks)} tracks)...")

    # Final padding fallback: if still under 10 tracks and total candidate pool >= 10, allow candidates already on active playlist
    if len(selected_tracks) < 10 and total_unique_candidate_bands >= 10:
        print(f"Applying final padding fallback (allowing active playlist artists if needed)...")
        for c in sorted_candidates:
            if len(selected_tracks) >= 10:
                break
            b_name = c["artist_name"]
            b_lower = b_name.lower()
            if b_lower in seen_bands:
                continue
            seen_bands.add(b_lower)
            selected_tracks.append(c)

    # Enforce constraints reporting:
    under_100k_count = sum(1 for t in selected_tracks if t.get("monthly_listeners", 0) < 100000)
    not_top50_count = sum(1 for t in selected_tracks if t["artist_name"].lower() not in top50_bands)

    if len(selected_tracks) < 10:
        print(f"ALERT / FAILURE: Could only find {len(selected_tracks)} songs satisfying Phase 2 rules!")

    if len(selected_tracks) > 0:
        if (under_100k_count / len(selected_tracks)) < 0.8:
            print("Warning: Listener count rule unsatisfied (< 80% under 100k listeners).")
        if (not_top50_count / len(selected_tracks)) < 0.5:
            print("Warning: Diversity check unsatisfied (< 50% not in top50).")

    print(f"Selection complete. Selected {len(selected_tracks)} tracks.")
    return selected_tracks

def generate_monday_playlist():
    print("--- Running Monday Playlist Generation Flow ---")

    # 1. Get write token
    token = get_spotify_write_token()
    if not token:
        print("Error: Could not obtain Spotify access token.")
        return

    # Check if we fell back to client credentials
    if token == get_spotify_token():
        print("\n" + "="*80)
        print("⚠️  WARNING: The obtained Spotify token is a Client Credentials token (not a User Write Token).")
        print("Playlist modifications (adding/pruning tracks) will fail with 401/403 errors")
        print("because Client Credentials cannot write to playlists. Please configure a valid SPOTIFY_REFRESH_TOKEN.")
        print("="*80 + "\n")

    playlist_id = "2ZqhNVOPmA3Nf0SRpzJ9Yz"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 2. Playlist Pruning check & gather active artists already in playlist upfront
    print("Checking playlist tracks for pruning (>84 days old) and current artists...")
    tracks_to_prune = []
    existing_playlist_artists = set()
    try:
        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
        time.sleep(0.5)
        res = requests.get(url, headers=headers, params={"limit": 100}, timeout=10)
        if res.status_code == 200:
            items = res.json().get("items", [])
            cutoff_date = datetime.now() - timedelta(days=84)
            for item in items:
                added_at_str = item.get("added_at")
                track = item.get("track")
                if added_at_str and track:
                    track_uri = track.get("uri")
                    # parse added_at, format: "2015-01-15T12:34:56Z"
                    try:
                        added_at = datetime.strptime(added_at_str[:19], "%Y-%m-%dT%H:%M:%S")
                        if added_at < cutoff_date:
                            print(f"Pruning: Track '{track.get('name')}' by '{track.get('artists')[0].get('name') if track.get('artists') else 'Unknown'}' is older than 84 days (Added: {added_at_str}).")
                            tracks_to_prune.append({"uri": track_uri})
                        else:
                            # Keep track of active artists already in the playlist
                            artists = track.get("artists", [])
                            if artists:
                                primary_artist_id = artists[0].get("id")
                                if primary_artist_id:
                                    existing_playlist_artists.add(primary_artist_id)
                    except Exception as pe:
                        print(f"Error parsing added_at date '{added_at_str}': {pe}")
        else:
            print(f"Warning: Could not fetch playlist items for pruning/artist tracking. Status: {res.status_code}")
    except Exception as e:
        print(f"Error during playlist pruning check: {e}")

    # 3. Progressive release window expansion: 7 days -> 14 -> 30 -> 60 -> 90 -> 180 days
    window_tiers = [7, 14, 30, 60, 90, 180]
    candidates = []
    selected = []

    for w_days in window_tiers:
        try:
            candidates = discover_punk_candidates(token, window_days=w_days, existing_candidates=candidates)
            selected = select_weekly_playlist_tracks(candidates, existing_playlist_artists=existing_playlist_artists)
            if len(selected) >= 10:
                print(f"Successfully selected {len(selected)} tracks at release window past {w_days} days!")
                break
            else:
                print(f"Release window past {w_days} days yielded {len(selected)} selected tracks (< 10 target). Expanding release window...")
        except Exception as e:
            print(f"Error during candidate discovery/selection for window past {w_days} days: {e}")

    if len(selected) < 10:
        print(f"\n" + "="*80)
        print(f"🚨 ALERT / FAILURE: Could only find {len(selected)} tracks meeting all Phase 2 rules across all release windows up to 180 days.")
        print("Rule requirement: Must hit exactly 10 songs. Halting workflow (no tracks added to Spotify).")
        print("="*80 + "\n")
        return

    # Delete pruned tracks if any
    if tracks_to_prune:
        print(f"Removing {len(tracks_to_prune)} expired tracks from playlist...")
        try:
            url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
            time.sleep(0.5)
            # Delete expects payload with "tracks": [{"uri": "..."}]
            res = requests.delete(url, headers=headers, json={"tracks": tracks_to_prune}, timeout=10)
            if res.status_code == 200:
                print("Pruned tracks successfully deleted.")
            else:
                print(f"Warning: Deleting pruned tracks returned status: {res.status_code}")
        except Exception as e:
            print(f"Error deleting pruned tracks: {e}")

    # 5. Add new selected tracks to top (position 0)
    track_uris = [f"spotify:track:{s['track_id']}" for s in selected]
    print(f"Adding {len(track_uris)} new tracks to the top of the playlist...")
    try:
        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
        payload = {
            "uris": track_uris,
            "position": 0
        }
        time.sleep(0.5)
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code in [200, 201]:
            print("Successfully populated weekly playlist tracks!")
        else:
            print(f"Warning: Failed to add tracks to Spotify playlist. Status code: {res.status_code}")
    except Exception as e:
        print(f"Error adding tracks to Spotify playlist: {e}")

    # 6. Insert into database `playlist_history` table
    if supabase:
        print("Logging selected tracks into playlist_history...")
        for s in selected:
            try:
                supabase.table("playlist_history").insert({
                    "track_id": s["track_id"],
                    "track_name": s["track_name"],
                    "artist_id": s["artist_id"],
                    "artist_name": s["artist_name"],
                    "tier": s["tier"],
                    "monthly_listeners": s["monthly_listeners"],
                    "release_date": s["release_date"]
                }).execute()
            except Exception as dbe:
                print(f"Error inserting track log into DB for {s['track_name']}: {dbe}")

        # 7. Deduplicate artist database / Tour Tracker Integration
        print("Integrating new artists with Tour Tracker...")
        existing_artist_names = set()
        try:
            start = 0
            page_size = 1000
            while True:
                res = supabase.table("artists").select("name").range(start, start + page_size - 1).execute()
                if not res.data:
                    break
                for row in res.data:
                    existing_artist_names.add(row["name"].lower())
                if len(res.data) < page_size:
                    break
                start += page_size
        except Exception as e:
            print(f"Error reading existing artists for integration: {e}")

        for s in selected:
            artist_name = s["artist_name"]
            artist_id = s["artist_id"]
            if artist_name.lower() not in existing_artist_names:
                print(f"New artist '{artist_name}' discovered. Inserting into Tour Tracker DB...")
                try:
                    supabase.table("artists").insert({
                        "name": artist_name,
                        "spotify_id": artist_id,
                        "source_playlist": "Punk in Progress",
                        "is_active": True,
                        "added_at": datetime.now().isoformat()
                    }).execute()
                    existing_artist_names.add(artist_name.lower())
                except Exception as ie:
                    print(f"Error inserting new artist '{artist_name}' into DB: {ie}")
            else:
                print(f"Artist '{artist_name}' already exists in DB. Skipping.")

    else:
        print("[No DB] Would log to history and integrate artists with Tour Tracker.")

def get_spotify_token():
    now = time.time()
    if _spotify_token_cache["token"] and _spotify_token_cache["expires_at"] > now + 60:
        return _spotify_token_cache["token"]

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None

    try:
        url = "https://accounts.spotify.com/api/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET
        }
        res = requests.post(url, data=data)
        res.raise_for_status()
        res_data = res.json()
        _spotify_token_cache["token"] = res_data["access_token"]
        _spotify_token_cache["expires_at"] = now + res_data.get("expires_in", 3600)
        return _spotify_token_cache["token"]
    except Exception as e:
        print(f"Error authenticating with Spotify: {e}")
        return None

def extract_playlist_id(url_or_uri):
    if not url_or_uri:
        return None
    # If it's a URI like spotify:playlist:4AGyN4LWzSqQXK1laupEaI
    if url_or_uri.startswith("spotify:playlist:"):
        return url_or_uri.split(":")[-1]

    # If it's a URL like https://open.spotify.com/playlist/4AGyN4LWzSqQXK1laupEaI?si=xxx
    if "playlist/" in url_or_uri:
        part = url_or_uri.split("playlist/")[-1]
        return part.split("?")[0].split("/")[0]

    return url_or_uri.strip()

def ingest_weekly_punk():
    print("--- Ingesting Weekly Punk Playlist ---")
    token = get_spotify_token()
    if not token:
        print("Error: Could not get Spotify token.")
        return

    playlist_id = "4AGyN4LWzSqQXK1laupEaI"
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        items = res.json().get("items", [])

        # Parse entries at positions 2 through 7 (index 1 to 6 inclusive)
        target_items = items[1:7]
        print(f"Fetched {len(target_items)} tracks from Weekly Punk playlist.")

        for item in target_items:
            track = item.get("track")
            if not track:
                continue

            artists = track.get("artists", [])
            if not artists:
                continue

            # Primary artist name and Spotify Artist ID
            primary_artist = artists[0]
            artist_name = primary_artist.get("name")
            spotify_id = primary_artist.get("id")

            if not artist_name:
                continue

            # Query database to check if artist already exists
            if supabase:
                try:
                    exist_res = supabase.table("artists").select("*").eq("name", artist_name).execute()
                    if exist_res.data:
                        # Artist exists, update added_at
                        print(f"Artist '{artist_name}' exists. Updating added_at...")
                        supabase.table("artists").update({
                            "added_at": datetime.now().isoformat()
                        }).eq("name", artist_name).execute()
                    else:
                        # New artist, insert with source_playlist = 'Weekly Punk'
                        print(f"Artist '{artist_name}' is new. Inserting...")
                        supabase.table("artists").insert({
                            "name": artist_name,
                            "spotify_id": spotify_id,
                            "source_playlist": "Weekly Punk",
                            "added_at": datetime.now().isoformat(),
                            "is_active": True
                        }).execute()
                except Exception as e:
                    print(f"Error database upserting artist {artist_name}: {e}")
            else:
                print(f"[No DB] Would upsert artist: {artist_name} ({spotify_id})")

    except Exception as e:
        print(f"Error fetching weekly playlist tracks: {e}")

def ingest_playlist_all(playlist_url_or_id):
    playlist_id = extract_playlist_id(playlist_url_or_id)
    if not playlist_id:
        print("Error: Could not extract playlist ID.")
        return

    print(f"--- Ingesting All Tracks from Playlist ID: {playlist_id} ---")
    token = get_spotify_token()
    if not token:
        print("Error: Could not get Spotify token.")
        return

    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

    limit = 100
    offset = 0
    all_items = []

    while True:
        params = {"limit": limit, "offset": offset}
        try:
            res = requests.get(url, headers=headers, params=params)
            res.raise_for_status()
            res_data = res.json()
            items = res_data.get("items", [])
            all_items.extend(items)

            if len(items) < limit or not res_data.get("next"):
                break
            offset += limit
            time.sleep(RATE_LIMIT_DELAY) # Rate limit delay
        except Exception as e:
            print(f"Error fetching playlist page at offset {offset}: {e}")
            break

    print(f"Fetched total of {len(all_items)} tracks from playlist.")

    existing_artists = set()
    if supabase:
        try:
            start = 0
            page_size = 1000
            while True:
                res = supabase.table("artists").select("name").range(start, start + page_size - 1).execute()
                if not res.data:
                    break
                existing_artists.update(a["name"] for a in res.data)
                if len(res.data) < page_size:
                    break
                start += page_size
        except Exception as e:
            print(f"Error fetching existing artists: {e}")

    new_artists_to_insert = []

    for item in all_items:
        track = item.get("track")
        if not track:
            continue

        artists = track.get("artists", [])
        if not artists:
            continue

        primary_artist = artists[0]
        artist_name = primary_artist.get("name")
        spotify_id = primary_artist.get("id")

        if not artist_name:
            continue

        if artist_name in existing_artists:
            print(f"Artist '{artist_name}' already exists. Skipping insertion.")
            continue

        if any(a["name"] == artist_name for a in new_artists_to_insert):
            continue

        new_artists_to_insert.append({
            "name": artist_name,
            "spotify_id": spotify_id,
            "source_playlist": f"Custom Playlist {playlist_id}",
            "added_at": datetime.now().isoformat(),
            "is_active": True
        })

    print(f"Found {len(new_artists_to_insert)} new artists to insert.")
    if supabase and new_artists_to_insert:
        try:
            chunk_size = 100
            for i in range(0, len(new_artists_to_insert), chunk_size):
                chunk = new_artists_to_insert[i:i + chunk_size]
                supabase.table("artists").insert(chunk).execute()
                print(f"Inserted chunk of {len(chunk)} new artists.")
        except Exception as e:
            print(f"Error bulk inserting new artists: {e}")
    elif not supabase:
        print(f"[No DB] Would insert new artists: {[a['name'] for a in new_artists_to_insert]}")

def robust_request(method, url, max_retries=3, initial_delay=2.0, **kwargs):
    global LASTFM_BLOCKED, DDG_BLOCKED, _DOMAIN_FAILURES

    # Check circuit breakers before even starting the request
    if "last.fm" in url and LASTFM_BLOCKED:
        print(f"Skipping request to {url} due to active Last.fm circuit breaker.")
        return None
    if "duckduckgo.com" in url and DDG_BLOCKED:
        print(f"Skipping request to {url} due to active DuckDuckGo circuit breaker.")
        return None

    # Ensure timeout is set
    if "timeout" not in kwargs:
        kwargs["timeout"] = 10

    if "headers" not in kwargs:
        kwargs["headers"] = {}
    if "User-Agent" not in kwargs["headers"]:
        kwargs["headers"]["User-Agent"] = random.choice(USER_AGENTS)

    domain_key = "last.fm" if "last.fm" in url else ("duckduckgo.com" if "duckduckgo.com" in url else None)

    delay = initial_delay
    for attempt in range(max_retries):
        try:
            # Pre-request delay to avoid hammering
            time.sleep(random.uniform(delay, delay + 1.5))
            if method.upper() == "POST":
                res = requests.post(url, **kwargs)
            else:
                res = requests.get(url, **kwargs)

            if 200 <= res.status_code < 300:
                if domain_key:
                    _DOMAIN_FAILURES[domain_key] = 0
                return res
            elif res.status_code in [403, 406, 429]:
                print(f"Warning: Got status code {res.status_code} for {url}.")
                if domain_key:
                    _DOMAIN_FAILURES[domain_key] += 1
                    if _DOMAIN_FAILURES[domain_key] >= _MAX_CONSECUTIVE_FAILURES:
                        print(f"Reached {_MAX_CONSECUTIVE_FAILURES} consecutive blocking responses for {domain_key}. Activating circuit breaker.")
                        if domain_key == "last.fm":
                            LASTFM_BLOCKED = True
                        elif domain_key == "duckduckgo.com":
                            DDG_BLOCKED = True
                return None
            else:
                res.raise_for_status()
        except Exception as e:
            print(f"Request attempt {attempt + 1} failed for {url}: {e}")
            if attempt == max_retries - 1:
                print(f"Max retries exceeded for {url}.")
                if domain_key:
                    _DOMAIN_FAILURES[domain_key] += 1
                    if _DOMAIN_FAILURES[domain_key] >= _MAX_CONSECUTIVE_FAILURES:
                        print(f"Reached {_MAX_CONSECUTIVE_FAILURES} consecutive failure errors for {domain_key}. Activating circuit breaker.")
                        if domain_key == "last.fm":
                            LASTFM_BLOCKED = True
                        elif domain_key == "duckduckgo.com":
                            DDG_BLOCKED = True
            delay *= 2

    return None

def is_valid_instagram_username(username):
    if not username or len(username) > 30:
        return False
    # Filter out common reserved path words
    blacklist = {
        "p", "reel", "reels", "stories", "tv", "explore", "developer", "about",
        "direct", "accounts", "emails", "legal", "help", "privacy", "terms",
        "tag", "tags", "explore", "challenge", "hacked", "directory", "linking"
    }
    if username.lower() in blacklist:
        return False
    # Instagram usernames can contain alphanumeric, periods, underscores, and must match format
    return bool(re.match(r"^[a-zA-Z0-9._]+$", username))

def resolve_instagram_via_search(artist_name):
    query = f'"{artist_name}" instagram.com'
    url = "https://lite.duckduckgo.com/lite/"
    headers = {
        "User-Agent": random.choice(USER_AGENTS)
    }
    try:
        res = robust_request("POST", url, data={"q": query}, headers=headers)
        if res is not None and res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Skip sharer links, duckduckgo redirects, and known subpaths
                if "instagram.com/" in href and "duckduckgo" not in href and "sharer" not in href:
                    # Parse the username
                    parts = href.split("instagram.com/")
                    if len(parts) > 1:
                        username_part = parts[1].split("/")[0].split("?")[0].strip()
                        if is_valid_instagram_username(username_part):
                            return f"https://www.instagram.com/{username_part}/"
    except Exception as e:
        print(f"Error resolving Instagram via search for {artist_name}: {e}")
    return None

def fetch_latest_instagram_post_shortcode(instagram_url, artist_name):
    if not instagram_url:
        return None
    parts = instagram_url.split("instagram.com/")
    if len(parts) < 2:
        return None
    username = parts[1].split("/")[0].split("?")[0].strip()
    if not username:
        return None

    query = f"site:instagram.com/p/ {username}"
    url = "https://lite.duckduckgo.com/lite/"
    headers = {
        "User-Agent": random.choice(USER_AGENTS)
    }
    try:
        res = robust_request("POST", url, data={"q": query}, headers=headers)
        if res is not None and res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "instagram.com/p/" in href and "duckduckgo" not in href:
                    post_parts = href.split("/p/")
                    if len(post_parts) > 1:
                        shortcode = post_parts[1].split("/")[0].split("?")[0].strip()
                        if shortcode:
                            return shortcode
    except Exception as e:
        print(f"Error fetching latest post shortcode for {artist_name}: {e}")
    return None

def resolve_instagram(artist_name, spotify_id=None):
    print(f"Resolving Instagram for '{artist_name}'...")

    # 1. Spotify check
    if spotify_id:
        token = get_spotify_token()
        if token:
            url = f"https://api.spotify.com/v1/artists/{spotify_id}"
            headers = {"Authorization": f"Bearer {token}"}
            try:
                res = requests.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    ext_urls = data.get("external_urls", {})
                    for key, val in ext_urls.items():
                        if "instagram" in key.lower() or "instagram.com" in val:
                            print(f"Found Instagram for '{artist_name}' on Spotify!")
                            return val
            except Exception as e:
                print(f"Error querying Spotify artist endpoint for {spotify_id}: {e}")

    # 2. Search fallback
    found_url = resolve_instagram_via_search(artist_name)
    if found_url:
        print(f"Resolved Instagram via search: {found_url}")
        return found_url

    print(f"Could not resolve Instagram for '{artist_name}'.")
    return None

def enrich_artists_instagram():
    if not supabase:
        return
    print("--- Running Instagram Page Resolver ---")
    try:
        # Limit Instagram resolutions per run to avoid rate limits on search engines
        res = supabase.table("artists")\
            .select("*")\
            .eq("is_active", True)\
            .is_("instagram_url", "null")\
            .limit(10)\
            .execute()
        artists = res.data if res.data else []
        print(f"Found {len(artists)} active artists without Instagram URL (processing up to 10).")
        for artist in artists:
            artist_id = artist["id"]
            artist_name = artist["name"]
            spotify_id = artist.get("spotify_id")

            instagram_url = resolve_instagram(artist_name, spotify_id)
            if instagram_url:
                supabase.table("artists").update({
                    "instagram_url": instagram_url
                }).eq("id", artist_id).execute()
                time.sleep(RATE_LIMIT_DELAY)
    except Exception as e:
        print(f"Error enriching artists with Instagram: {e}")

def is_target_country(country_raw, target_countries):
    """
    Safely matches raw country string against target countries.
    For 2-letter codes, enforces exact equality (e.g. 'ES' != 'Estonia').
    For longer names, checks substring match (case-insensitive).
    """
    if not country_raw:
        return False
    c_raw_clean = country_raw.strip().upper()
    c_raw_lower = country_raw.strip().lower()
    for target in target_countries:
        target_clean = target.strip().upper()
        if len(target_clean) == 2:
            if c_raw_clean == target_clean:
                return True
        else:
            if target_clean.lower() in c_raw_lower:
                return True
    return False

def get_tours_bandsintown(band_name, countries=None):
    """
    Query Bandsintown for upcoming tours of a band.
    Filters for target countries (default: Spain 'ES', Portugal 'PT') and future dates.
    """
    if countries is None:
        countries = ['ES', 'PT', 'Spain', 'Portugal', 'España', 'Espanha']

    app_id = BANDSINTOWN_API_KEY or os.environ.get("BANDSINTOWN_API_KEY") or "concertscout"
    today_str = datetime.now().date().isoformat()

    try:
        time.sleep(RATE_LIMIT_DELAY)
        url = f"https://rest.bandsintown.com/artists/{urllib.parse.quote(band_name, safe='')}/events"
        params = {"app_id": app_id}
        res = requests.get(url, params=params, timeout=10)

        if res.status_code == 404:
            return []
        if res.status_code != 200:
            print(f"Bandsintown API status {res.status_code} for band '{band_name}'")
            return []

        all_events = res.json()
        if not isinstance(all_events, list):
            return []

        filtered_events = []
        for event in all_events:
            venue = event.get("venue", {})
            country_raw = venue.get("country", "")
            city_raw = venue.get("city", "Unknown")

            # Check country filter safely
            if not is_target_country(country_raw, countries):
                continue

            event_date_str = event.get("datetime", "")
            if not event_date_str:
                continue

            try:
                event_date = datetime.fromisoformat(event_date_str.replace("Z", "+00:00")).date().isoformat()
                if event_date < today_str:
                    continue
            except Exception:
                continue

            ticket_url = None
            offers = event.get("offers", [])
            if offers and isinstance(offers, list):
                ticket_url = offers[0].get("url")

            # Standardized tour event dict
            country_code = "ES" if is_target_country(country_raw, ["ES", "Spain", "España"]) else ("PT" if is_target_country(country_raw, ["PT", "Portugal", "Espanha"]) else country_raw.strip().upper())

            tour_event = {
                "band_name": band_name,
                "venue": venue.get("name", "Unknown Venue"),
                "city": city_raw,
                "country": country_code,
                "event_date": event_date,
                "ticket_url": ticket_url,
                "last_fm_url": ticket_url,  # Included for backward compatibility with app.py UI
                "source": "bandsintown",
                "scraped_at": datetime.now().isoformat()
            }
            filtered_events.append(tour_event)

        return filtered_events
    except Exception as e:
        print(f"Bandsintown tour fetch failed for '{band_name}': {e}")
        return []

def get_tours_songkick(band_name, countries=None):
    """
    Query Songkick API for upcoming tours of a band (requires SONGKICK_API_KEY).
    Filters for target countries (default: Spain 'ES', Portugal 'PT') and future dates.
    """
    key = SONGKICK_API_KEY or os.environ.get("SONGKICK_API_KEY")
    if not key:
        return []

    if countries is None:
        countries = ['ES', 'PT', 'Spain', 'Portugal', 'España', 'Espanha']

    today_str = datetime.now().date().isoformat()

    try:
        # Step 1: Search artist ID
        time.sleep(RATE_LIMIT_DELAY)
        search_url = f"https://api.songkick.com/api/3.0/search/artists.json"
        res = requests.get(search_url, params={"query": band_name, "apikey": key}, timeout=10)
        if res.status_code != 200:
            return []

        search_data = res.json()
        results = search_data.get("resultsPage", {}).get("results", {}).get("artist", [])
        if not results:
            return []

        artist_id = results[0].get("id")
        if not artist_id:
            return []

        # Step 2: Fetch calendar events
        time.sleep(RATE_LIMIT_DELAY)
        cal_url = f"https://api.songkick.com/api/3.0/artists/{artist_id}/calendar.json"
        res_cal = requests.get(cal_url, params={"apikey": key}, timeout=10)
        if res_cal.status_code != 200:
            return []

        events_data = res_cal.json().get("resultsPage", {}).get("results", {}).get("event", [])
        if not isinstance(events_data, list):
            return []

        filtered_events = []
        for event in events_data:
            venue = event.get("venue", {})
            location = event.get("location", {})
            country_raw = location.get("country", "") or venue.get("metroArea", {}).get("country", {}).get("displayName", "")
            city_raw = location.get("city", "") or venue.get("metroArea", {}).get("displayName", "Unknown")

            if not is_target_country(country_raw, countries):
                continue

            event_date_str = event.get("start", {}).get("date", "")
            if not event_date_str or event_date_str < today_str:
                continue

            ticket_url = event.get("uri")
            country_code = "ES" if is_target_country(country_raw, ["ES", "Spain", "España"]) else ("PT" if is_target_country(country_raw, ["PT", "Portugal", "Espanha"]) else country_raw.strip().upper())

            tour_event = {
                "band_name": band_name,
                "venue": venue.get("displayName", "Unknown Venue"),
                "city": city_raw,
                "country": country_code,
                "event_date": event_date_str,
                "ticket_url": ticket_url,
                "last_fm_url": ticket_url,
                "source": "songkick",
                "scraped_at": datetime.now().isoformat()
            }
            filtered_events.append(tour_event)

        return filtered_events
    except Exception as e:
        print(f"Songkick tour fetch failed for '{band_name}': {e}")
        return []

def deduplicate_tours(bandsintown_events, songkick_events):
    """
    Merge and deduplicate tour events from multiple sources by (venue, event_date).
    Bandsintown events take priority over Songkick events.
    """
    merged = list(bandsintown_events)
    seen_keys = {f"{e['venue'].lower().strip()}:{e['event_date']}" for e in bandsintown_events}

    for sk_event in songkick_events:
        key = f"{sk_event['venue'].lower().strip()}:{sk_event['event_date']}"
        if key not in seen_keys:
            seen_keys.add(key)
            merged.append(sk_event)

    merged.sort(key=lambda x: x["event_date"])
    return merged

def save_tour_events_to_supabase(band_name, tours, supabase_client=None):
    """
    Save list of tour events to Supabase tour_events table.
    Uses UPSERT on unique constraint (band_name, venue, event_date).
    """
    client = supabase_client or supabase
    if not tours or not client:
        return 0

    saved_count = 0
    for tour in tours:
        try:
            client.table("tour_events").upsert(
                tour, on_conflict="band_name,venue,event_date"
            ).execute()
            saved_count += 1
        except Exception as e:
            print(f"Save tour event error for '{band_name}': {e}")
    return saved_count

def track_tour_events():
    """
    Main tour scraping pipeline replacing Last.fm.
    Scrapes Bandsintown and Songkick for all registered bands and upserts tour events to Supabase.
    """
    if not supabase:
        print("Warning: Supabase client not initialized. Skipping tour event scraping.")
        return

    print("--- Scraping Bandsintown & Songkick Tour Events for All Bands ---")
    try:
        band_names = set()

        try:
            ws_res = supabase.table("weekly_submissions").select("band_name").execute()
            if ws_res.data:
                for r in ws_res.data:
                    if r.get("band_name"):
                        band_names.add(r["band_name"].strip())
        except Exception as ws_e:
            print(f"Error reading weekly_submissions for tour scraping: {ws_e}")

        try:
            br_res = supabase.table("band_registry").select("band_name").execute()
            if br_res.data:
                for r in br_res.data:
                    if r.get("band_name"):
                        band_names.add(r["band_name"].strip())
        except Exception as br_e:
            print(f"Error reading band_registry for tour scraping: {br_e}")

        try:
            art_res = supabase.table("artists").select("name").eq("is_active", True).execute()
            if art_res.data:
                for r in art_res.data:
                    if r.get("name"):
                        band_names.add(r["name"].strip())
        except Exception as art_e:
            print(f"Error reading artists for tour scraping: {art_e}")

        sorted_bands = sorted(list(band_names))
        print(f"Found total of {len(sorted_bands)} unique bands to check for tour events.")

        total_events_found = 0
        total_events_saved = 0

        for idx, band in enumerate(sorted_bands, 1):
            bt_tours = get_tours_bandsintown(band)
            sk_tours = get_tours_songkick(band)
            combined_tours = deduplicate_tours(bt_tours, sk_tours)

            if combined_tours:
                saved = save_tour_events_to_supabase(band, combined_tours, supabase)
                total_events_found += len(combined_tours)
                total_events_saved += saved
                print(f"✓ [{idx}/{len(sorted_bands)}] {band}: {len(combined_tours)} tours found, {saved} saved.")

        print(f"Tour scraping complete. Found {total_events_found} events, saved {total_events_saved} events across {len(sorted_bands)} bands.")
    except Exception as e:
        print(f"Error in tour event scraping pipeline: {e}")

# Backward compatibility alias
track_lastfm_concerts = track_tour_events
scrape_lastfm_artist_events = get_tours_bandsintown

def check_instagram_tour_keywords(artist_name, instagram_url):
    if not instagram_url:
        return False, []

    print(f"Checking Instagram content for '{artist_name}' at {instagram_url}...")
    query = f"site:instagram.com {artist_name}"
    url = "https://lite.duckduckgo.com/lite/"
    headers = {
        "User-Agent": random.choice(USER_AGENTS)
    }

    trigger_words = ["spain", "madrid", "barcelona", "tour", "gira", "concert", "concierto", "portugal", "lisbon", "lisboa", "porto"]
    found_keywords = []

    try:
        res = robust_request("POST", url, data={"q": query}, headers=headers)
        if res is not None and res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            snippets = []
            for td in soup.find_all("td", class_="result-snippet"):
                snippets.append(td.get_text().lower())

            full_text = " ".join(snippets)
            for word in trigger_words:
                if word in full_text:
                    found_keywords.append(word)

            if found_keywords:
                print(f"Matches found on Instagram snippet scan for '{artist_name}': {found_keywords}")
                return True, found_keywords
    except Exception as e:
        print(f"Error scanning Instagram snippets for {artist_name}: {e}")

    return False, []

def scan_instagram_enrichment():
    if not supabase:
        return
    print("--- Scanning Instagram for Tour Keywords ---")
    try:
        # Fetch existing concerts to extract artist_ids with scheduled concerts
        concert_res = supabase.table("concerts").select("artist_id").execute()
        artists_with_concerts = {c["artist_id"] for c in concert_res.data} if concert_res.data else set()

        res = supabase.table("artists").select("*").eq("is_active", True).execute()
        artists = res.data if res.data else []

        # Filter out artists that already have listed concerts
        artists_to_scan = [a for a in artists if a["id"] not in artists_with_concerts]

        # Shuffle remaining and check at most 10 random artists per run
        random.shuffle(artists_to_scan)
        selected_artists = artists_to_scan[:10]

        print(f"Scanning up to 10 random active artists without existing concerts (out of {len(artists_to_scan)} candidates)...")

        for artist in selected_artists:
            instagram_url = artist.get("instagram_url")
            if instagram_url:
                is_flagged, matched = check_instagram_tour_keywords(artist["name"], instagram_url)
                if is_flagged:
                    print(f"⚠️ MANUAL REVIEW REQUIRED: Artist '{artist['name']}' has potential tour info! Matched keywords: {matched}")
                # Additional safety delay between keyword scans
                time.sleep(random.uniform(2.0, 4.0))
    except Exception as e:
        print(f"Error scanning Instagram enrichment: {e}")

def sweep_past_concerts():
    if not supabase:
        print("Warning: Supabase client not initialized. Skipping past concert sweeper.")
        return
    print("--- Running Past Concert Sweeper ---")
    try:
        today = datetime.now().date().isoformat()
        res = supabase.table("concerts").delete().lt("event_date", today).execute()
        deleted_count = len(res.data) if res.data else 0
        print(f"Successfully deleted {deleted_count} stale concerts older than {today}.")
    except Exception as e:
        print(f"Error sweeping past concerts: {e}")

def run_enrichment_pipeline():
    print("--- Running Ingestion/Enrichment Pipeline ---")
    # Resolve Instagram URL for any active artists missing it
    enrich_artists_instagram()
    # Match concerts on Last.fm
    track_lastfm_concerts()
    # Experimental keyword checks on Instagram
    scan_instagram_enrichment()

def calculate_momentum_score(wow_growth_pct, mom_growth_pct, total_growth_pct, trajectory="flat", total_features=0, total_shares=0, latest_listener_count=0, avg_growth_after_share_pct=0.0):
    wow = float(wow_growth_pct or 0)
    mom = float(mom_growth_pct or 0)
    tot = float(total_growth_pct or 0)

    # 1. Growth Rate Component (Up to 50 points)
    # WoW growth score (Up to 25 pts)
    wow_score = 0.0
    if wow > 0:
        wow_score = min(25.0, 10.0 + (wow * 1.5))

    # MoM growth score (Up to 15 pts)
    mom_score = 0.0
    if mom > 0:
        mom_score = min(15.0, 5.0 + (mom * 0.5))

    # Total growth score (Up to 10 pts)
    tot_score = 0.0
    if tot > 0:
        tot_score = min(10.0, tot * 0.1)

    growth_component = wow_score + mom_score + tot_score

    # 2. Trajectory Bonus Component (Up to 15 points)
    traj_score = 0.0
    if trajectory == "explosive":
        traj_score = 15.0
    elif trajectory == "steady":
        traj_score = 5.0

    # 3. Engagement & Activity Component (Up to 20 points)
    # Featured in playlists (5 pts per feature, max 10)
    feat_score = min(10.0, float(total_features or 0) * 5.0)
    # Social shares (5 pts per share, max 10)
    share_score = min(10.0, float(total_shares or 0) * 5.0)

    engagement_component = feat_score + share_score

    # 4. Scale Baseline & Share Lift Component (Up to 15 points)
    scale_score = 0.0
    listeners = int(latest_listener_count or 0)
    if listeners >= 100000:
        scale_score = 10.0
    elif listeners >= 10000:
        scale_score = 7.0
    elif listeners >= 1000:
        scale_score = 5.0
    elif listeners > 0:
        scale_score = 2.0

    lift_score = 0.0
    if float(avg_growth_after_share_pct or 0) > 0:
        lift_score = 5.0

    scale_component = scale_score + lift_score

    total_score = growth_component + traj_score + engagement_component + scale_component
    score = int(round(total_score))

    # Floor at 0 for negative growth unless offset by engagement, cap at 100
    return max(0, min(100, score))

def determine_trajectory(snapshots_desc):
    """
    Given a list of snapshot dicts ordered by recorded_date DESC.
    Compares recent vs older listener counts to assign trajectory.
    """
    if not snapshots_desc:
        return "flat"
    if len(snapshots_desc) == 1:
        count = snapshots_desc[0].get("listener_count") or 0
        return "steady" if count > 0 else "flat"

    recent = int(snapshots_desc[0].get("listener_count") or 0)
    # Compare with snapshot from 4+ positions back, or oldest available
    compare_idx = 4 if len(snapshots_desc) > 4 else len(snapshots_desc) - 1
    previous = int(snapshots_desc[compare_idx].get("listener_count") or 0)

    if previous <= 0:
        if recent >= 1000:
            return "explosive"
        elif recent > 0:
            return "steady"
        else:
            return "flat"

    gain = recent - previous

    # Requiring both relative growth > 30% AND absolute gain >= 50 listeners to avoid micro-noise
    if recent > previous * 1.3 and gain >= 50:
        return "explosive"
    elif recent >= previous * 0.9:
        return "steady"
    elif recent > 0 and recent >= previous * 0.7:
        return "flat"
    else:
        return "declining"

def search_spotify_artist(band_name):
    token = get_spotify_token()
    if not token:
        return None, 0
    url = "https://api.spotify.com/v1/search"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": band_name, "type": "artist", "limit": 1}
    time.sleep(0.5)
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            items = res.json().get("artists", {}).get("items", [])
            if items:
                art = items[0]
                return art.get("id"), art.get("followers", {}).get("total", 0)
    except Exception as e:
        print(f"Error searching Spotify artist for '{band_name}': {e}")
    return None, 0

def take_band_listener_snapshots():
    if not supabase:
        print("Warning: Supabase client not initialized. Skipping band listener snapshots.")
        return

    print("--- Running Band Listener Snapshot Collection ---")
    band_map = {}  # band_name_lower -> {"band_name": str, "spotify_id": str}

    # 1. Collect unique bands from weekly_submissions
    try:
        ws_res = supabase.table("weekly_submissions").select("band_name").execute()
        if ws_res.data:
            for r in ws_res.data:
                bname = r.get("band_name", "").strip()
                if bname and bname.lower() not in band_map:
                    band_map[bname.lower()] = {"band_name": bname, "spotify_id": None}
    except Exception as e:
        print(f"Error reading weekly_submissions for snapshot: {e}")

    # 2. Collect unique bands from band_registry
    try:
        br_res = supabase.table("band_registry").select("band_name, spotify_id").execute()
        if br_res.data:
            for r in br_res.data:
                bname = r.get("band_name", "").strip()
                sp_id = r.get("spotify_id")
                if bname:
                    b_lower = bname.lower()
                    if b_lower not in band_map:
                        band_map[b_lower] = {"band_name": bname, "spotify_id": sp_id}
                    elif sp_id and not band_map[b_lower]["spotify_id"]:
                        band_map[b_lower]["spotify_id"] = sp_id
    except Exception as e:
        print(f"Error reading band_registry for snapshot: {e}")

    # 3. Collect unique bands from playlist_history
    try:
        ph_res = supabase.table("playlist_history").select("artist_name, artist_id").execute()
        if ph_res.data:
            for r in ph_res.data:
                bname = r.get("artist_name", "").strip()
                sp_id = r.get("artist_id")
                if bname:
                    b_lower = bname.lower()
                    if b_lower not in band_map:
                        band_map[b_lower] = {"band_name": bname, "spotify_id": sp_id}
                    elif sp_id and not band_map[b_lower]["spotify_id"]:
                        band_map[b_lower]["spotify_id"] = sp_id
    except Exception as e:
        print(f"Error reading playlist_history for snapshot: {e}")

    # 4. Collect unique bands from artists table
    try:
        art_res = supabase.table("artists").select("name, spotify_id").eq("is_active", True).execute()
        if art_res.data:
            for r in art_res.data:
                bname = r.get("name", "").strip()
                sp_id = r.get("spotify_id")
                if bname:
                    b_lower = bname.lower()
                    if b_lower not in band_map:
                        band_map[b_lower] = {"band_name": bname, "spotify_id": sp_id}
                    elif sp_id and not band_map[b_lower]["spotify_id"]:
                        band_map[b_lower]["spotify_id"] = sp_id
    except Exception as e:
        print(f"Error reading artists for snapshot: {e}")

    print(f"Found {len(band_map)} unique bands to snapshot.")
    today_str = datetime.now().date().isoformat()
    current_week = f"W{datetime.now().isocalendar()[1]}"

    success_count = 0
    fail_count = 0

    for b_lower, info in band_map.items():
        bname = info["band_name"]
        sp_id = info["spotify_id"]
        followers = 0

        if not sp_id:
            sp_id, followers = search_spotify_artist(bname)

        listener_count = 0
        if sp_id:
            listener_count = get_monthly_listeners(sp_id)

        if listener_count == 0 and not sp_id:
            print(f"Could not resolve Spotify metrics for '{bname}'. Skipping.")
            fail_count += 1
            continue

        snapshot_payload = {
            "band_name": bname,
            "spotify_id": sp_id,
            "listener_count": listener_count,
            "follower_count": followers,
            "recorded_date": today_str,
            "snapshot_week": current_week,
            "source": "spotify_api"
        }

        try:
            supabase.table("band_listener_snapshot").upsert(
                snapshot_payload, on_conflict="band_name,recorded_date"
            ).execute()
            success_count += 1
        except Exception as e:
            print(f"Error upserting snapshot for '{bname}': {e}")
            fail_count += 1

    total_attempted = success_count + fail_count
    success_rate = (success_count / total_attempted * 100) if total_attempted > 0 else 100
    print(f"Snapshot run complete: {success_count}/{total_attempted} bands updated ({success_rate:.1f}% success rate).")
    if total_attempted > 0 and success_rate < 80.0:
        print("🚨 ALERT: Snapshot success rate fell below 80% threshold!")

def recalculate_analytics_summary():
    if not supabase:
        print("Warning: Supabase client not initialized. Skipping analytics recalculation.")
        return

    print("--- Recalculating Band Analytics Summary ---")

    # Fetch feature history per band from weekly_submissions & playlist_history
    featured_history = {}  # band_lower -> {"weeks": set()}
    share_history = {}     # band_lower -> list of dicts: {"week": str, "week_num": int, "created_at": str}

    try:
        ws_res = supabase.table("weekly_submissions").select("band_name, week, shared, created_at").execute()
        if ws_res.data:
            for r in ws_res.data:
                bname = r.get("band_name", "").strip().lower()
                week = r.get("week")
                is_shared = r.get("shared", False)
                c_at = r.get("created_at", "")
                if bname and week:
                    if bname not in featured_history:
                        featured_history[bname] = {"weeks": set()}
                    featured_history[bname]["weeks"].add(week)

                    if is_shared:
                        if bname not in share_history:
                            share_history[bname] = []
                        match = re.search(r"\d+", str(week))
                        w_num = int(match.group(0)) if match else 0
                        share_history[bname].append({
                            "week": str(week),
                            "week_num": w_num,
                            "created_at": c_at
                        })
    except Exception as e:
        print(f"Error reading weekly_submissions feature and share history: {e}")

    try:
        ph_res = supabase.table("playlist_history").select("artist_name, added_at").execute()
        if ph_res.data:
            for r in ph_res.data:
                bname = r.get("artist_name", "").strip().lower()
                added_at = r.get("added_at")
                if bname and added_at:
                    try:
                        dt = datetime.fromisoformat(added_at.replace("Z", "+00:00"))
                        w_str = f"W{dt.isocalendar()[1]}"
                    except Exception:
                        w_str = f"W{datetime.now().isocalendar()[1]}"
                    if bname not in featured_history:
                        featured_history[bname] = {"weeks": set()}
                    featured_history[bname]["weeks"].add(w_str)
    except Exception as e:
        print(f"Error reading playlist_history feature history: {e}")

    # Query all snapshots ordered by recorded_date ASC
    try:
        start = 0
        page_size = 1000
        all_snapshots = []
        while True:
            res = supabase.table("band_listener_snapshot").select("*").range(start, start + page_size - 1).order("recorded_date", desc=False).execute()
            if not res.data:
                break
            all_snapshots.extend(res.data)
            if len(res.data) < page_size:
                break
            start += page_size
    except Exception as e:
        print(f"Error fetching snapshots for summary recalculation: {e}")
        return

    # Group by normalized band_name (case-insensitive) to prevent duplicates
    band_snapshots = {} # b_lower -> list of snapshots
    band_canonical_names = {} # b_lower -> original case band name

    for s in all_snapshots:
        raw_bname = s.get("band_name", "").strip()
        if not raw_bname:
            continue
        b_lower = raw_bname.lower()
        if b_lower not in band_snapshots:
            band_snapshots[b_lower] = []
            band_canonical_names[b_lower] = raw_bname
        band_snapshots[b_lower].append(s)

    print(f"Recalculating analytics summary for {len(band_snapshots)} bands with snapshot data...")

    summary_records = []
    for b_lower, snaps in band_snapshots.items():
        bname = band_canonical_names.get(b_lower, b_lower)
        # Sorted by date ASC
        snaps.sort(key=lambda x: str(x.get("recorded_date", "")))

        first_snap = snaps[0]
        latest_snap = snaps[-1]

        sp_id = latest_snap.get("spotify_id") or first_snap.get("spotify_id")
        first_date_str = str(first_snap.get("recorded_date", ""))
        latest_date_str = str(latest_snap.get("recorded_date", ""))
        latest_count = int(latest_snap.get("listener_count") or 0)

        # Peak calculation
        peak_count = 0
        peak_date_str = latest_date_str
        for s in snaps:
            lc = int(s.get("listener_count") or 0)
            if lc >= peak_count:
                peak_count = lc
                peak_date_str = str(s.get("recorded_date", ""))

        # Days tracked
        try:
            d_first = datetime.strptime(first_date_str[:10], "%Y-%m-%d").date()
            d_latest = datetime.strptime(latest_date_str[:10], "%Y-%m-%d").date()
            days_tracked = max(1, (d_latest - d_first).days)
        except Exception:
            days_tracked = 1

        # Helper to find first non-zero snapshot count in list
        def get_first_nonzero_count(snap_list):
            for s in snap_list:
                val = int(s.get("listener_count") or 0)
                if val > 0:
                    return val
            return 0

        # WoW Growth calculation (~7 days back)
        wow_growth = 0.00
        if len(snaps) > 1:
            prev_7d_snap = None
            try:
                latest_dt = datetime.strptime(latest_date_str[:10], "%Y-%m-%d").date()
                for s in reversed(snaps[:-1]):
                    s_dt = datetime.strptime(str(s.get("recorded_date"))[:10], "%Y-%m-%d").date()
                    if (latest_dt - s_dt).days >= 5:
                        prev_7d_snap = s
                        break
            except Exception:
                pass
            if not prev_7d_snap:
                prev_7d_snap = snaps[-2]

            prev_count = int(prev_7d_snap.get("listener_count") or 0)
            if prev_count == 0:
                prev_count = get_first_nonzero_count(snaps[:-1])

            if prev_count > 0:
                wow_growth = round(((latest_count - prev_count) / float(prev_count)) * 100.0, 2)
            elif latest_count > 0:
                wow_growth = 100.00

        # MoM Growth calculation (~30 days back)
        mom_growth = 0.00
        if len(snaps) > 1:
            prev_30d_snap = None
            try:
                latest_dt = datetime.strptime(latest_date_str[:10], "%Y-%m-%d").date()
                for s in reversed(snaps[:-1]):
                    s_dt = datetime.strptime(str(s.get("recorded_date"))[:10], "%Y-%m-%d").date()
                    if (latest_dt - s_dt).days >= 25:
                        prev_30d_snap = s
                        break
            except Exception:
                pass
            if not prev_30d_snap and len(snaps) >= 4:
                prev_30d_snap = snaps[0]

            if prev_30d_snap:
                prev_m_count = int(prev_30d_snap.get("listener_count") or 0)
                if prev_m_count == 0:
                    prev_m_count = get_first_nonzero_count(snaps[:-1])

                if prev_m_count > 0:
                    mom_growth = round(((latest_count - prev_m_count) / float(prev_m_count)) * 100.0, 2)
                elif latest_count > 0:
                    mom_growth = 100.00

        # Total growth since first snapshot
        first_count = int(first_snap.get("listener_count") or 0)
        if first_count == 0:
            first_count = get_first_nonzero_count(snaps)

        if first_count > 0:
            total_growth = round(((latest_count - first_count) / float(first_count)) * 100.0, 2)
        else:
            total_growth = 0.00

        momentum = calculate_momentum_score(wow_growth, mom_growth, total_growth)

        # Trajectory based on snapshots DESC
        snaps_desc = list(reversed(snaps))
        trajectory = determine_trajectory(snaps_desc)

        # Feature info
        f_weeks = sorted(list(featured_history.get(b_lower, {}).get("weeks", [])))
        first_feat_week = f_weeks[0] if f_weeks else None
        last_feat_week = f_weeks[-1] if f_weeks else None
        tot_features = len(f_weeks)

        # Share history and lift metrics
        shares = share_history.get(b_lower, [])
        total_shares = len(shares)
        was_shared = total_shares > 0
        last_shared_week = None
        listener_count_at_share = 0
        listener_count_1week_after_share = 0
        share_lift_pct = 0.00
        share_lift_absolute = 0
        avg_growth_after_share_pct = 0.00

        if was_shared:
            # Sort shares chronologically by week_num then created_at
            shares.sort(key=lambda x: (x["week_num"], x["created_at"]))
            last_shared_week = shares[-1]["week"]

            lifts = []
            latest_valid_lift = None

            for share in shares:
                s_week = share["week"]
                s_num = share["week_num"]

                # Find snapshot for share week
                share_snap = next((s for s in snaps if s.get("snapshot_week") == s_week), None)
                if not share_snap and s_num > 0:
                    share_snap = next((s for s in snaps if re.search(r"\d+", str(s.get("snapshot_week") or "")) and int(re.search(r"\d+", str(s.get("snapshot_week"))).group(0)) == s_num), None)
                if not share_snap and snaps:
                    # Fallback to closest snapshot or latest available
                    share_snap = snaps[-1]

                # Find snapshot for week after share
                next_snap = next((s for s in snaps if s.get("snapshot_week") == f"W{s_num + 1}"), None)
                if not next_snap and share_snap:
                    try:
                        s_dt = datetime.strptime(str(share_snap.get("recorded_date"))[:10], "%Y-%m-%d").date()
                        for ns in snaps:
                            ns_dt = datetime.strptime(str(ns.get("recorded_date"))[:10], "%Y-%m-%d").date()
                            if 5 <= (ns_dt - s_dt).days <= 10:
                                next_snap = ns
                                break
                    except Exception:
                        pass

                if share_snap:
                    at_cnt = int(share_snap.get("listener_count") or 0)
                    after_cnt = int(next_snap.get("listener_count") or 0) if next_snap else latest_count
                    if at_cnt > 0:
                        l_pct = round(((after_cnt - at_cnt) / float(at_cnt)) * 100.0, 2)
                        l_abs = after_cnt - at_cnt
                        if next_snap:
                            lifts.append(l_pct)
                        latest_valid_lift = {
                            "at": at_cnt,
                            "after": after_cnt,
                            "pct": l_pct,
                            "abs": l_abs
                        }

            if lifts:
                avg_growth_after_share_pct = round(sum(lifts) / float(len(lifts)), 2)

            if latest_valid_lift:
                listener_count_at_share = latest_valid_lift["at"]
                listener_count_1week_after_share = latest_valid_lift["after"]
                share_lift_pct = latest_valid_lift["pct"]
                share_lift_absolute = latest_valid_lift["abs"]

        momentum = calculate_momentum_score(
            wow_growth, mom_growth, total_growth,
            trajectory=trajectory,
            total_features=tot_features,
            total_shares=total_shares,
            latest_listener_count=latest_count,
            avg_growth_after_share_pct=avg_growth_after_share_pct
        )

        summary_payload = {
            "band_name": bname,
            "spotify_id": sp_id,
            "first_snapshot_date": first_date_str if first_date_str else None,
            "latest_listener_count": latest_count,
            "latest_snapshot_date": latest_date_str if latest_date_str else None,
            "week_over_week_growth_pct": float(wow_growth),
            "month_over_month_growth_pct": float(mom_growth),
            "total_growth_since_first_snapshot": float(total_growth),
            "momentum_score": momentum,
            "growth_trajectory": trajectory,
            "peak_listener_count": peak_count,
            "peak_date": peak_date_str if peak_date_str else None,
            "first_featured_week": first_feat_week,
            "last_featured_week": last_feat_week,
            "total_features": tot_features,
            "days_tracked": days_tracked,
            "total_shares": total_shares,
            "was_shared": was_shared,
            "last_shared_week": last_shared_week,
            "listener_count_at_share": listener_count_at_share,
            "listener_count_1week_after_share": listener_count_1week_after_share,
            "share_lift_pct": float(share_lift_pct),
            "share_lift_absolute": share_lift_absolute,
            "avg_growth_after_share_pct": float(avg_growth_after_share_pct),
            "updated_at": datetime.now().isoformat()
        }

        summary_records.append(summary_payload)

    if summary_records:
        try:
            for rec in summary_records:
                supabase.table("band_analytics_summary").upsert(
                    rec, on_conflict="band_name"
                ).execute()
            print(f"Successfully upserted {len(summary_records)} band analytics summaries.")
        except Exception as e:
            print(f"Error upserting band_analytics_summary records: {e}")

def main():
    parser = argparse.ArgumentParser(description="ConcertScout Ingest & Enrichment Pipeline")
    parser.add_argument("--playlist", type=str, help="On-demand playlist ingestion URL/ID (Module B)")
    parser.add_argument("--weekly", action="store_true", help="Run the Wednesday Automated Ingestion (Module A)")
    parser.add_argument("--monday-playlist", action="store_true", help="Run the Monday Automated Playlist Curation")
    parser.add_argument("--analytics", action="store_true", help="Run the Sunday Band Analytics Snapshot & Recalculation")

    args = parser.parse_args()

    # Automatically sweep and delete past concerts older than today
    sweep_past_concerts()

    if args.playlist:
        ingest_playlist_all(args.playlist)
        run_enrichment_pipeline()
    elif args.weekly:
        ingest_weekly_punk()
        run_enrichment_pipeline()
    elif args.monday_playlist:
        generate_monday_playlist()
    elif args.analytics:
        take_band_listener_snapshots()
        recalculate_analytics_summary()
    else:
        # Default runs the full enrichment pipeline for existing active bands
        run_enrichment_pipeline()

if __name__ == "__main__":
    main()
