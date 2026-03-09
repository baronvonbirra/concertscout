import os
import requests
from supabase import create_client, Client
from dotenv import load_dotenv
import time

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
    print("Warning: BANDSINTOWN_APP_ID not set. Event fetching will be skipped.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

def get_core_artists():
    if not supabase:
        return []
    response = supabase.table("artists").select("*").eq("is_core", True).execute()
    return response.data

def fetch_bandsintown_events(artist_name):
    if not BANDSINTOWN_APP_ID:
        return []
    url = f"https://rest.bandsintown.com/artists/{artist_name}/events"
    params = {"app_id": BANDSINTOWN_APP_ID}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        events = response.json()
        if not isinstance(events, list):
            return []

        spain_events = []
        for event in events:
            if event.get('venue', {}).get('country') == 'Spain':
                spain_events.append({
                    "artist": artist_name,
                    "city": event.get('venue', {}).get('city'),
                    "venue": event.get('venue', {}).get('name'),
                    "date": event.get('datetime', '').split('T')[0],
                    "ticket_url": event.get('url'),
                })
        return spain_events
    except Exception as e:
        print(f"Error fetching Bandsintown events for {artist_name}: {e}")
        return []

def get_similar_punk_artists(artist_name):
    if not LASTFM_API_KEY:
        return []

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
        similar_artists = [a['name'] for a in data.get('similarartists', {}).get('artist', [])]

        punk_artists = []
        for sa in similar_artists:
            tags_params = {
                "method": "artist.gettoptags",
                "artist": sa,
                "api_key": LASTFM_API_KEY,
                "format": "json"
            }
            tags_response = requests.get(similar_url, params=tags_params)
            tags_response.raise_for_status()
            tags_data = tags_response.json()
            tags = [t['name'].lower() for t in tags_data.get('toptags', {}).get('tag', [])]

            punk_keywords = ['punk', 'hardcore', 'crust', 'post-punk']
            is_punk = any(keyword in ' '.join(tags) for keyword in punk_keywords)

            if is_punk:
                print(f"  [PASSED] {sa} (Tags: {', '.join(tags[:5])}...)")
                punk_artists.append(sa)
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
            count += 1
        except Exception as e:
            print(f"Error upserting event {event_data}: {e}")
    return count

def main():
    print("Starting Last-Scout...")
    core_artists = get_core_artists()
    print(f"Found {len(core_artists)} core artists.")

    total_new = 0
    core_names = {a['name'] for a in core_artists}

    for artist in core_artists:
        name = artist['name']
        print(f"--- Checking core artist: {name} ---")

        # 1. Core artist events
        core_events = fetch_bandsintown_events(name)
        new_core = upsert_events(core_events, False)
        total_new += new_core
        print(f"Upserted {new_core} core events for {name}.")

        # 2. Discovery logic
        print(f"Finding similar punk artists for {name}...")
        similar_punks = get_similar_punk_artists(name)
        # Filter out core artists from recommendations to prevent overwriting
        similar_punks = [sa for sa in similar_punks if sa not in core_names]
        print(f"Found validated similar artists: {', '.join(similar_punks)}")

        for sa in similar_punks:
            sa_events = fetch_bandsintown_events(sa)
            new_rec = upsert_events(sa_events, True)
            total_new += new_rec
            if new_rec > 0:
                print(f"Found and upserted {new_rec} recommended events for {sa}.")

    print(f"Scout complete. Total events processed: {total_new}")

if __name__ == "__main__":
    main()
