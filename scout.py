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

load_dotenv()

_monthly_listeners_cache = {}

# Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    print("Warning: Supabase credentials not fully set.")
if not LASTFM_API_KEY:
    print("Warning: LASTFM_API_KEY not set. Last.fm event matching will be skipped.")
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

RATE_LIMIT_DELAY = 0.5

# Circuit breaker flags to prevent hammering when blocked
LASTFM_BLOCKED = False
DDG_BLOCKED = False

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
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9"
    }

    # Scout V2.0 rule: mandatory 0.5-second delay before all external API calls
    time.sleep(0.5)

    try:
        # Avoid circuit breaker if possible but respect connection errors
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

def discover_punk_candidates(token):
    print("--- Starting Candidate Discovery and Classification ---")
    headers = {"Authorization": f"Bearer {token}"}
    candidates = {}

    # 28 days release window definition
    execution_date = datetime.now().date()
    start_date = execution_date - timedelta(days=28)

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

    def is_eligible(track_name, album_name, release_date_str):
        rel_date = parse_release_date(release_date_str)
        if not rel_date or rel_date < start_date or rel_date > execution_date:
            return False

        # Filter track name and album name to exclude remixes, live, compilation, etc.
        excluded_keywords = ["remix", "compilation", "live", "acoustic", "demo", "instrumental", "remaster", "mix"]
        text = f"{track_name} {album_name}".lower()
        for kw in excluded_keywords:
            # Match word boundary or prefix/suffix to avoid filtering out words like 'live' in 'alive'
            if re.search(rf"\b{kw}\b", text):
                return False
        return True

    # --- Source 1: Active Database Artists ---
    active_artists = []
    if supabase:
        try:
            res = supabase.table("artists").select("id, name, spotify_id").eq("is_active", True).execute()
            active_artists = res.data if res.data else []
        except Exception as e:
            print(f"Error fetching active artists for candidate seeding: {e}")

    print(f"Seeding candidates from {len(active_artists)} active database artists...")
    for artist in active_artists:
        artist_id = artist.get("spotify_id")
        if not artist_id:
            continue

        # Fetch artist albums/singles
        url = f"https://api.spotify.com/v1/artists/{artist_id}/albums"
        params = {"include_groups": "single,album", "market": "ES", "limit": 20}
        time.sleep(0.5) # rate limit delay
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                albums = res.json().get("items", [])
                for album in albums:
                    album_id = album.get("id")
                    album_name = album.get("name", "")
                    release_date_str = album.get("release_date")

                    # Basic date filtering before querying tracks
                    album_date = parse_release_date(release_date_str)
                    if not album_date or album_date < start_date or album_date > execution_date:
                        continue

                    # Fetch tracks of this album
                    tracks_url = f"https://api.spotify.com/v1/albums/{album_id}/tracks"
                    time.sleep(0.5)
                    t_res = requests.get(tracks_url, headers=headers, params={"limit": 50}, timeout=10)
                    if t_res.status_code == 200:
                        tracks = t_res.json().get("items", [])
                        for track in tracks:
                            track_id = track.get("id")
                            track_name = track.get("name", "")

                            if is_eligible(track_name, album_name, release_date_str):
                                candidates[track_id] = {
                                    "track_id": track_id,
                                    "track_name": track_name,
                                    "album_name": album_name,
                                    "release_date": release_date_str,
                                    "artist_id": artist_id,
                                    "artist_name": artist.get("name"),
                                    "spotify_id": artist_id
                                }
        except Exception as e:
            print(f"Error fetching albums for artist {artist.get('name')}: {e}")

    # --- Source 2: Broad Punk Genre Searches ---
    genres = ["punk", "pop punk", "hardcore", "skate punk", "post-hardcore", "emo", "screamo"]
    print(f"Searching broad Spotify genre queries for {genres}...")
    for genre in genres:
        url = "https://api.spotify.com/v1/search"
        params = {
            "q": f'genre:"{genre}"',
            "type": "track",
            "market": "ES",
            "limit": 50
        }
        time.sleep(0.5)
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                tracks = res.json().get("tracks", {}).get("items", [])
                for track in tracks:
                    track_id = track.get("id")
                    track_name = track.get("name", "")
                    album = track.get("album", {})
                    album_name = album.get("name", "")
                    release_date_str = album.get("release_date")

                    if is_eligible(track_name, album_name, release_date_str):
                        artists = track.get("artists", [])
                        if artists:
                            primary_artist = artists[0]
                            artist_id = primary_artist.get("id")
                            artist_name = primary_artist.get("name")

                            if track_id not in candidates:
                                candidates[track_id] = {
                                    "track_id": track_id,
                                    "track_name": track_name,
                                    "album_name": album_name,
                                    "release_date": release_date_str,
                                    "artist_id": artist_id,
                                    "artist_name": artist_name,
                                    "spotify_id": artist_id
                                }
        except Exception as e:
            print(f"Error performing genre search for '{genre}': {e}")

    # --- Fetch Monthly Listeners & Classify Tiers ---
    print(f"Discovered total of {len(candidates)} raw track candidates. Fetching monthly listeners...")
    classified_candidates = []

    for t_id, item in candidates.items():
        artist_id = item["artist_id"]
        listeners = get_monthly_listeners(artist_id)

        # Classify Tier
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
        print(f"Candidate: '{item['track_name']}' by '{item['artist_name']}' | Listeners: {listeners} | Tier: {tier} | Released: {item['release_date']}")

    return classified_candidates

def select_weekly_playlist_tracks(candidates):
    print("--- Selecting Tracks for Weekly Discovery Playlist ---")
    if not candidates:
        print("Warning: No candidates found.")
        return []

    # Get historical data from Supabase
    historical_track_ids = set()
    recent_artist_ids = set()

    if supabase:
        try:
            # Fetch all historical tracks
            start = 0
            page_size = 1000
            while True:
                res = supabase.table("playlist_history").select("track_id").range(start, start + page_size - 1).execute()
                if not res.data:
                    break
                for row in res.data:
                    historical_track_ids.add(row["track_id"])
                if len(res.data) < page_size:
                    break
                start += page_size

            # Fetch artists added in the last 30 days
            thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
            recent_res = supabase.table("playlist_history").select("artist_id").gte("added_at", thirty_days_ago).execute()
            if recent_res.data:
                for row in recent_res.data:
                    recent_artist_ids.add(row["artist_id"])

            print(f"Loaded {len(historical_track_ids)} historical track IDs and {len(recent_artist_ids)} recently featured artist IDs from DB.")
        except Exception as e:
            print(f"Error querying playlist history from database: {e}")

    # Filter candidates
    eligible_candidates = []
    for c in candidates:
        if c["track_id"] in historical_track_ids:
            # Duplicate track check
            continue
        if c["artist_id"] in recent_artist_ids:
            # Artist 30 days repeat cap
            continue
        eligible_candidates.append(c)

    print(f"Remaining eligible candidates after de-duplication: {len(eligible_candidates)}")

    # Group by tier
    tiers_map = {
        "Major": [],
        "Mid": [],
        "Indie": [],
        "Emerging": []
    }
    for c in eligible_candidates:
        t = c["tier"]
        if t in tiers_map:
            tiers_map[t].append(c)

    # Required distribution
    required = {
        "Major": 1,
        "Mid": 2,
        "Indie": 5,
        "Emerging": 2
    }

    selected_tracks = []
    for tier, count in required.items():
        candidates_in_tier = list(tiers_map[tier])
        print(f"Tier {tier}: Found {len(candidates_in_tier)} candidates. Need {count}.")

        # If we have enough, we randomly sample
        if len(candidates_in_tier) >= count:
            selected = random.sample(candidates_in_tier, count)
            selected_tracks.extend(selected)
        else:
            print(f"Warning: Not enough candidates in Tier {tier}. Relaxation logic triggered.")
            # relaxation step 1: allow artists featured in last 30 days (but still no track duplicates)
            relaxed_candidates = [c for c in candidates if c["tier"] == tier and c["track_id"] not in historical_track_ids]
            # De-duplicate artist within this list to prevent selecting same artist twice in same week
            seen_artists_this_week = set()
            clean_relaxed = []
            for c in relaxed_candidates:
                if c["artist_id"] not in seen_artists_this_week:
                    clean_relaxed.append(c)
                    seen_artists_this_week.add(c["artist_id"])

            if len(clean_relaxed) >= count:
                selected = random.sample(clean_relaxed, count)
                selected_tracks.extend(selected)
                print(f"Successfully fulfilled Tier {tier} count using relaxed artist-cap criteria.")
            else:
                # relaxation step 2: take whatever we can find in this tier, and if still short, fill from other tiers to ensure exactly 10 tracks
                selected_tracks.extend(clean_relaxed)
                print(f"Warning: Could only find {len(clean_relaxed)} total tracks for Tier {tier} after relaxation.")

    # If we are STILL short of exactly 10 tracks, we must pad from other tiers to reach exactly 10 (maintaining DoD count)
    total_needed = 10
    if len(selected_tracks) < total_needed:
        still_needed = total_needed - len(selected_tracks)
        print(f"Warning: Selection has {len(selected_tracks)} tracks. Padding {still_needed} tracks from any available tier...")
        # Get any candidates that are not currently selected or in historical track ids
        already_selected_ids = {s["track_id"] for s in selected_tracks}
        pad_candidates = [c for c in candidates if c["track_id"] not in historical_track_ids and c["track_id"] not in already_selected_ids]

        # Randomly select padding candidates
        if len(pad_candidates) >= still_needed:
            padded = random.sample(pad_candidates, still_needed)
            selected_tracks.extend(padded)
        else:
            # Last resort: just use any pad candidates we can get
            selected_tracks.extend(pad_candidates)

    # Ensure exactly 10 tracks!
    selected_tracks = selected_tracks[:10]
    print(f"Selection complete. Selected {len(selected_tracks)} tracks.")
    for idx, t in enumerate(selected_tracks):
        print(f"[{idx+1}] Track: '{t['track_name']}' | Artist: '{t['artist_name']}' | Tier: {t['tier']} | Listeners: {t['monthly_listeners']}")

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

    # 2. Discover candidates
    try:
        candidates = discover_punk_candidates(token)
    except Exception as e:
        print(f"Error during candidate discovery: {e}")
        return

    # 3. Track selection
    try:
        selected = select_weekly_playlist_tracks(candidates)
    except Exception as e:
        print(f"Error during track selection: {e}")
        return

    if not selected:
        print("Warning: No tracks were selected. Exiting playlist generation.")
        return

    playlist_id = "2ZqhNVOPmA3Nf0SRpzJ9Yz"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 4. Playlist Pruning (> 84 days old)
    print("Checking playlist tracks for pruning (>84 days old)...")
    tracks_to_prune = []
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
                    except Exception as pe:
                        print(f"Error parsing added_at date '{added_at_str}': {pe}")
        else:
            print(f"Warning: Could not fetch playlist items for pruning. Status: {res.status_code}")
    except Exception as e:
        print(f"Error during playlist pruning check: {e}")

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
    global LASTFM_BLOCKED, DDG_BLOCKED

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
                return res
            elif res.status_code in [403, 406, 429]:
                print(f"Warning: Got status code {res.status_code} for {url}. Blocking further requests to this domain.")
                if "last.fm" in url:
                    LASTFM_BLOCKED = True
                if "duckduckgo.com" in url:
                    DDG_BLOCKED = True
                return None
            else:
                res.raise_for_status()
        except Exception as e:
            print(f"Request attempt {attempt + 1} failed for {url}: {e}")
            if attempt == max_retries - 1:
                # Trigger circuit breaker if we reach the last retry attempt on connection issues
                print(f"Max retries exceeded for {url}. Activating circuit breaker.")
                if "last.fm" in url:
                    LASTFM_BLOCKED = True
                if "duckduckgo.com" in url:
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

def scrape_lastfm_artist_events(artist_name):
    safe_artist = artist_name.replace(" ", "+")
    url = f"https://www.last.fm/music/{safe_artist}/+events"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    events = []
    try:
        res = robust_request("GET", url, headers=headers)
        if res is None:
            return []
        if res.status_code == 406:
            print(f"Warning: Last.fm rate limited or blocked (406) for artist: {artist_name}")
            return []

        soup = BeautifulSoup(res.text, "html.parser")

        items = soup.find_all(["tr", "li", "div"], class_=lambda x: x and ("events-list-item" in x or "event-list-item" in x or "events-item" in x))

        if not items:
            event_links = [a for a in soup.find_all("a", href=True) if "/event/" in a["href"]]
            seen_events = set()
            for link in event_links:
                href = link["href"]
                if href in seen_events:
                    continue
                seen_events.add(href)

                parent = link.find_parent(["tr", "li", "div", "article"])
                if parent:
                    items.append(parent)

        for item in items:
            event_link_tag = item.find("a", href=lambda x: x and "/event/" in x)
            if not event_link_tag:
                continue

            event_url = f"https://www.last.fm{event_link_tag['href']}"
            event_name = event_link_tag.get_text(strip=True)

            date_str = None
            time_tag = item.find("time")
            if time_tag and time_tag.get("datetime"):
                date_str = time_tag["datetime"].split("T")[0]
            else:
                date_elem = item.find(class_=lambda x: x and "date" in x.lower())
                if date_elem:
                    date_str = date_elem.get_text(strip=True)

            if not date_str:
                date_str = datetime.now().date().isoformat()

            venue = "Unknown Venue"
            city = "Unknown City"
            country = "Unknown Country"

            venue_elem = item.find(class_=lambda x: x and ("venue" in x.lower() or "location" in x.lower()))
            if venue_elem:
                text_parts = [p.strip() for p in venue_elem.get_text(separator=",").split(",") if p.strip()]
                if len(text_parts) > 0:
                    venue = text_parts[0]
                if len(text_parts) > 1:
                    city = text_parts[1]
                if len(text_parts) > 2:
                    country = text_parts[-1]
            else:
                text_content = item.get_text(separator=",").replace("\n", ",")
                parts = [p.strip() for p in text_content.split(",") if p.strip()]
                if len(parts) > 2:
                    venue = parts[1]
                    city = parts[2]
                    if len(parts) > 3:
                        country = parts[-1]

            events.append({
                "event_name": event_name,
                "city": city,
                "venue": venue,
                "event_date": date_str,
                "ticket_url": event_url,
                "country": country,
                "source": "lastfm"
            })

    except Exception as e:
        print(f"Error scraping Last.fm events for {artist_name}: {e}")

    return events

def track_lastfm_concerts():
    if not supabase:
        return
    print("--- Scraping Last.fm Events for Active Artists ---")
    try:
        # Calculate 7 days ago timestamp
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        # Select active artists where last_scraped_at is null OR older than 7 days, limit to 15 per run
        res = supabase.table("artists")\
            .select("*")\
            .eq("is_active", True)\
            .or_(f"last_scraped_at.is.null,last_scraped_at.lt.{seven_days_ago}")\
            .limit(15)\
            .execute()

        artists = res.data if res.data else []
        print(f"Found {len(artists)} active artists due for scraping (limit 15 per run).")

        for artist in artists:
            artist_id = artist["id"]
            artist_name = artist["name"]
            instagram_url = artist.get("instagram_url")

            # Resolve latest Instagram post shortcode for state tracking/memorization
            latest_shortcode = fetch_latest_instagram_post_shortcode(instagram_url, artist_name)

            scraped_events = scrape_lastfm_artist_events(artist_name)
            valid_concerts = []

            for event in scraped_events:
                country = event.get("country", "")
                if country.lower() in ["spain", "portugal", "españa", "espanha"]:
                    valid_concerts.append({
                        "artist_id": artist_id,
                        "event_name": event["event_name"],
                        "city": event["city"],
                        "venue": event["venue"],
                        "event_date": event["event_date"],
                        "ticket_url": event["ticket_url"],
                        "source": "lastfm"
                    })

            if valid_concerts:
                print(f"Found {len(valid_concerts)} concerts in Spain/Portugal for '{artist_name}'.")
                for concert in valid_concerts:
                    try:
                        supabase.table("concerts").upsert(
                            concert, on_conflict="artist_id,event_date,city"
                        ).execute()
                    except Exception as e:
                        print(f"Error upserting concert {concert}: {e}")
            else:
                print(f"No concerts in Spain/Portugal found for '{artist_name}'.")

            # Update scraping and state tracking timestamps in DB
            try:
                update_payload = {
                    "last_scraped_at": datetime.now().isoformat()
                }
                if latest_shortcode:
                    update_payload["last_instagram_post_id"] = latest_shortcode

                supabase.table("artists").update(update_payload).eq("id", artist_id).execute()
            except Exception as e:
                print(f"Error updating scraper state for {artist_name}: {e}")

            time.sleep(random.uniform(2.0, 4.0))
    except Exception as e:
        print(f"Error in tracking last.fm concerts: {e}")

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

def main():
    parser = argparse.ArgumentParser(description="ConcertScout Ingest & Enrichment Pipeline")
    parser.add_argument("--playlist", type=str, help="On-demand playlist ingestion URL/ID (Module B)")
    parser.add_argument("--weekly", action="store_true", help="Run the Wednesday Automated Ingestion (Module A)")
    parser.add_argument("--monday-playlist", action="store_true", help="Run the Monday Automated Playlist Curation")

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
    else:
        # Default runs the full enrichment pipeline for existing active bands
        run_enrichment_pipeline()

if __name__ == "__main__":
    main()
