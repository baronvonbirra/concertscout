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

load_dotenv()

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

# Spotify Auth Cache
_spotify_token_cache = {"token": None, "expires_at": 0}

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

            if res.status_code == 200:
                return res
            elif res.status_code in [403, 406, 429]:
                print(f"Warning: Got status code {res.status_code} for {url}. Backing off...")
                delay *= 2
            else:
                res.raise_for_status()
        except Exception as e:
            print(f"Request attempt {attempt + 1} failed for {url}: {e}")
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
        res = supabase.table("artists").select("*").eq("is_active", True).is_("instagram_url", "null").execute()
        artists = res.data if res.data else []
        print(f"Found {len(artists)} active artists without Instagram URL.")
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

    args = parser.parse_args()

    # Automatically sweep and delete past concerts older than today
    sweep_past_concerts()

    if args.playlist:
        ingest_playlist_all(args.playlist)
        run_enrichment_pipeline()
    elif args.weekly:
        ingest_weekly_punk()
        run_enrichment_pipeline()
    else:
        # Default runs the full enrichment pipeline for existing active bands
        run_enrichment_pipeline()

if __name__ == "__main__":
    main()
