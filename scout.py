import os
import requests
from supabase import create_client, Client
from dotenv import load_dotenv
import time
import random
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import json

load_dotenv()

# Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY")
BANDSINTOWN_APP_ID = os.environ.get("BANDSINTOWN_APP_ID")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    print("Warning: Supabase credentials not fully set.")
if not LASTFM_API_KEY:
    print("Warning: LASTFM_API_KEY not set. Discovery will be skipped.")
if not BANDSINTOWN_APP_ID:
    print("Warning: BANDSINTOWN_APP_ID not set. Bandsintown fetching will be skipped.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# Limits and Rate Limiting
LAST_FM_LOOKUP_LIMIT = 20
TOTAL_SCAN_LIMIT = 200
RATE_LIMIT_DELAY = 0.5

# Global counters
last_fm_lookups_performed = 0

# Summary dictionary for Mission 6
summary = {
    "new_spain_tours": 0,
    "proximity_gigs": 0,
    "linktree_changes": 0,
    "events_upserted": 0
}

def get_core_artists():
    if not supabase:
        return []
    response = supabase.table("artists").select("*").eq("is_core", True).execute()
    return response.data

def get_rotation_artists(limit):
    if not supabase:
        return []
    # Fetch non-core artists ordered by last_checked ASC, nulls first
    response = (supabase.table("artists")
                .select("*")
                .eq("is_core", False)
                .order("last_checked", nullsfirst=True)
                .limit(limit)
                .execute())
    return response.data

def update_artist_last_checked(artist_id):
    if not supabase:
        return
    try:
        supabase.table("artists").update({
            "last_checked": datetime.now().isoformat()
        }).eq("id", artist_id).execute()
    except Exception as e:
        print(f"Error updating last_checked for artist {artist_id}: {e}")

def get_artists_patches():
    if not supabase:
        return {}
    response = supabase.table("artists").select("name, bandsintown_patch").execute()
    return {a['name']: a['bandsintown_patch'] for a in response.data if a.get('bandsintown_patch')}

def is_proximity_event(country, city):
    if country == 'Spain':
        return False

    if country in ['Portugal', 'Andorra']:
        return True

    # French border cities
    border_cities = ['Biarritz', 'Perpignan', 'Toulouse']
    if country == 'France' and city in border_cities:
        return True

    return False

def fetch_bandsintown_events(artist_name, patch=None):
    if not BANDSINTOWN_APP_ID:
        return []

    # Rate limiting
    time.sleep(RATE_LIMIT_DELAY)

    artist_id = patch if patch else artist_name
    url = f"https://rest.bandsintown.com/artists/{artist_id}/events"
    params = {"app_id": BANDSINTOWN_APP_ID}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        events = response.json()
        if not isinstance(events, list):
            return []

        filtered_events = []
        for event in events:
            venue = event.get('venue', {})
            country = venue.get('country')
            city = venue.get('city')

            is_proximity = is_proximity_event(country, city)

            if country == 'Spain' or is_proximity:
                filtered_events.append({
                    "artist": artist_name,
                    "city": city,
                    "venue": venue.get('name'),
                    "date": event.get('datetime', '').split('T')[0],
                    "ticket_url": event.get('url'),
                    "source": "Bandsintown",
                    "is_proximity": is_proximity
                })
        return filtered_events
    except Exception as e:
        print(f"Error fetching Bandsintown events for {artist_name}: {e}")
        return []

def fetch_all_sources(artist_name, patch=None):
    """
    Fetches events for a given artist. Currently only uses Bandsintown.
    """
    return fetch_bandsintown_events(artist_name, patch)

def linktree_sniffer(artist):
    url = artist.get('linktree_url')
    priority = artist.get('priority_level')

    # Mission 3: Only run this if linktree_url is present and priority_level is high
    if not url or str(priority).lower() not in ['high', '10', 'top']:
        return

    print(f"--- Running Linktree Sniffer for {artist['name']} ---")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
    }

    # Rate limiting
    time.sleep(RATE_LIMIT_DELAY)
    # Stealth mode: random sleep
    time.sleep(random.uniform(2, 5))

    try:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Extract all button text
        # Linktree buttons are usually <a> tags with specific data-testid or just inside certain divs
        buttons = soup.find_all('a', attrs={'data-testid': 'LinkButton'})
        if not buttons:
            # Fallback for different Linktree layouts
            buttons = soup.find_all('div', attrs={'data-testid': 'LinkButton'})

        button_texts = sorted([b.get_text().strip() for b in buttons if b.get_text()])
        current_snapshot = json.dumps(button_texts)

        last_snapshot = artist.get('last_linktree_snapshot')

        if last_snapshot != current_snapshot:
            print(f"!!! LINKTREE UPDATE DETECTED for {artist['name']} !!!")
            summary['linktree_changes'] += 1
            # Update snapshot in DB
            supabase.table("artists").update({
                "last_linktree_snapshot": current_snapshot
            }).eq("id", artist['id']).execute()
        else:
            print(f"No changes for {artist['name']} Linktree.")

    except Exception as e:
        print(f"Error sniffing Linktree for {artist['name']}: {e}")

def get_similar_punk_artists(artist_id, artist_name):
    global last_fm_lookups_performed
    if not LASTFM_API_KEY:
        return []

    # Mission 4: Cache logic
    # Check similar_artists_cache
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        cached = supabase.table("similar_artists_cache").select("*").eq("parent_artist_id", artist_id).gt("last_updated", thirty_days_ago).execute()
        if cached.data:
            print(f"Using cached similar artists for {artist_name}")
            return [a['similar_artist_name'] for a in cached.data]
    except Exception as e:
        print(f"Error checking cache for {artist_name}: {e}")

    if last_fm_lookups_performed >= LAST_FM_LOOKUP_LIMIT:
        print(f"Last.fm lookup limit reached. Skipping discovery for {artist_name}.")
        return []

    last_fm_lookups_performed += 1

    print(f"Fetching fresh similar artists for {artist_name} from Last.fm")

    # Rate limiting
    time.sleep(RATE_LIMIT_DELAY)

    similar_url = "https://ws.audioscrobbler.com/2.0/"
    params = {
        "method": "artist.getsimilar",
        "artist": artist_name,
        "api_key": LASTFM_API_KEY,
        "format": "json",
        "limit": 5
    }

    try:
        response = requests.get(similar_url, params=params)
        response.raise_for_status()
        data = response.json()
        similar_artists = data.get('similarartists', {}).get('artist', [])

        punk_artists = []

        # Clear old cache for this artist
        supabase.table("similar_artists_cache").delete().eq("parent_artist_id", artist_id).execute()

        for sa_data in similar_artists:
            sa = sa_data['name']
            match_score = sa_data.get('match')

            tags_params = {
                "method": "artist.gettoptags",
                "artist": sa,
                "api_key": LASTFM_API_KEY,
                "format": "json"
            }
            # Rate limiting
            time.sleep(RATE_LIMIT_DELAY)
            tags_response = requests.get(similar_url, params=tags_params)
            tags_response.raise_for_status()
            tags_data = tags_response.json()
            tags = [t['name'].lower() for t in tags_data.get('toptags', {}).get('tag', [])]

            punk_keywords = ['punk', 'hardcore', 'crust', 'post-punk']
            is_punk = any(keyword in ' '.join(tags) for keyword in punk_keywords)

            if is_punk:
                print(f"  [PASSED] {sa} (Tags: {', '.join(tags[:5])}...)")
                punk_artists.append(sa)

                # Save to cache
                supabase.table("similar_artists_cache").insert({
                    "parent_artist_id": artist_id,
                    "similar_artist_name": sa,
                    "match_score": match_score,
                    "last_updated": datetime.now().isoformat()
                }).execute()
            else:
                print(f"  [FAILED] {sa} (Tags: {', '.join(tags[:5])}...)")

        return punk_artists
    except Exception as e:
        print(f"Error with Last.fm for {artist_name}: {e}")
        return []

def upsert_events(events, is_recommendation):
    if not supabase or not events:
        return 0

    count = 0
    for event in events:
        event_data = {
            **event,
            "is_recommendation": is_recommendation
        }
        try:
            # Using upsert with on_conflict to prevent duplicates based on artist, city, date
            response = supabase.table("events").upsert(
                event_data, on_conflict="artist,city,date"
            ).execute()

            if event_data.get('is_proximity'):
                summary['proximity_gigs'] += 1
            elif not is_recommendation:
                summary['new_spain_tours'] += 1

            count += 1
        except Exception as e:
            print(f"Error upserting event {event_data}: {e}")

    summary['events_upserted'] += count
    return count

def main():
    print("Starting PUNK-SCOUT V2.0...")
    core_artists = get_core_artists()
    print(f"Found {len(core_artists)} core artists.")

    # Smart Scheduling: Fetch rotation artists
    remaining_quota = max(0, TOTAL_SCAN_LIMIT - len(core_artists))
    rotation_artists = get_rotation_artists(remaining_quota)
    print(f"Rotation: fetched {len(rotation_artists)} artists.")

    # Process all selected artists
    all_to_process = core_artists + rotation_artists
    print(f"Total artists to process this run: {len(all_to_process)}")

    # Get all available patches
    patches = get_artists_patches()
    core_names = {a['name'] for a in core_artists}

    for artist in all_to_process:
        name = artist['name']
        artist_id = artist['id']
        is_core = artist.get('is_core', False)
        patch = artist.get('bandsintown_patch')
        print(f"--- Processing {'core' if is_core else 'rotation'} artist: {name} ---")

        # Mission 3: Linktree Sniffer
        linktree_sniffer(artist)

        # Mission 2: Omni-Search
        print(f"Fetching events for {name}...")
        events = fetch_all_sources(name, patch=patch)
        # Rotation artists are official artists, not recommendations.
        new_events_count = upsert_events(events, False)
        print(f"Upserted {new_events_count} events for {name}.")

        # Mission 4: Discovery Logic (Only for Core Artists)
        if is_core:
            print(f"Finding similar punk artists for core artist {name}...")
            similar_punks = get_similar_punk_artists(artist_id, name)
            # Filter out core artists from recommendations
            similar_punks = [sa for sa in similar_punks if sa not in core_names]
            print(f"Validated similar artists: {', '.join(similar_punks)}")

            for sa in similar_punks:
                print(f"Checking events for similar artist: {sa}...")
                sa_patch = patches.get(sa)
                sa_events = fetch_all_sources(sa, patch=sa_patch)
                new_rec = upsert_events(sa_events, True)
                if new_rec > 0:
                    print(f"Found and upserted {new_rec} recommended events for {sa}.")

        # Update last_checked
        update_artist_last_checked(artist_id)

    print("\n--- SCOUT SUMMARY ---")
    print(f"Total events upserted: {summary['events_upserted']}")
    print(f"New Spain tours found: {summary['new_spain_tours']}")
    print(f"Proximity gigs detected: {summary['proximity_gigs']}")
    print(f"Linktree changes detected: {summary['linktree_changes']}")
    print("----------------------")

if __name__ == "__main__":
    main()
