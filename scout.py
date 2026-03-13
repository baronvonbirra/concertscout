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

# User-Agent list for scraper rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
]

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

def get_all_artists_names():
    if not supabase:
        return set()
    response = supabase.table("artists").select("name").execute()
    return {a['name'] for a in response.data}

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

def get_locations():
    if not supabase:
        return []
    response = supabase.table("locations").select("*").execute()
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

def scrape_songkick_city(city_id, country, city_name):
    """
    Scrapes Songkick metro area pages for upcoming events, with pagination support.
    Covers all events until the end of 2026.
    """
    base_url = f"https://www.songkick.com/metro-areas/{city_id}-{country.lower()}-{city_name.lower().replace(' ', '-')}"

    all_events = []
    page = 1
    max_pages = 20 # Safety limit

    while page <= max_pages:
        url = f"{base_url}?page={page}"
        print(f"  Scraping page {page} of {city_name}...")

        # User-Agent Rotation (Requirement: browser-like string to avoid 403 Forbidden)
        headers = {"User-Agent": random.choice(USER_AGENTS)}

        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            listings = soup.find_all('li', class_='event-listing')
            if not listings:
                print(f"    No more events found on page {page}.")
                break

            page_events_count = 0
            for li in listings:
                # Artist: <strong>
                artist_tag = li.find('strong')
                artist_name = artist_tag.get_text(strip=True) if artist_tag else "Unknown"

                # Date: time[datetime]
                time_tag = li.find('time')
                date_str = time_tag.get('datetime').split('T')[0] if time_tag and time_tag.get('datetime') else "Unknown"

                # Stop if we hit 2027
                if date_str != "Unknown" and date_str >= "2027-01-01":
                    print(f"    Reached date {date_str}, stopping pagination.")
                    return all_events

                # Venue: .location span
                venue_tag = li.find('span', class_='location')
                venue = venue_tag.get_text(strip=True) if venue_tag else "Unknown"

                # Ticket URL: a.event-link
                link_tag = li.find('a', class_='event-link')
                event_url = f"https://www.songkick.com{link_tag.get('href')}" if link_tag and link_tag.get('href') else "#"

                all_events.append({
                    "artist": artist_name,
                    "date": date_str,
                    "venue": venue,
                    "ticket_url": event_url,
                    "city": city_name,
                    "source": "Songkick",
                    "is_proximity": is_proximity_event(country, city_name)
                })
                page_events_count += 1

            print(f"    Found {page_events_count} events on page {page}.")

            # If we found less than a full page (usually 50 events on Songkick), we might be at the end
            # But safer to check for next page link if we want to be absolutely sure.
            # However, the loop will naturally break if next page has no listings.
            page += 1

        except Exception as e:
            print(f"    Error scraping {city_name} page {page}: {e}")
            break

    return all_events

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

            # Requirement: Last.fm Tag Checker (Filter: punk, hardcore, ska, or oi)
            punk_keywords = ['punk', 'hardcore', 'ska', 'oi', 'crust', 'post-punk']
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

def get_artist_tags(artist_name):
    """
    Fetches top tags for an artist from Last.fm.
    """
    if not LASTFM_API_KEY:
        return []

    time.sleep(RATE_LIMIT_DELAY)
    url = "https://ws.audioscrobbler.com/2.0/"
    params = {
        "method": "artist.gettoptags",
        "artist": artist_name,
        "api_key": LASTFM_API_KEY,
        "format": "json"
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        tags = [t['name'].lower() for t in data.get('toptags', {}).get('tag', [])]
        return tags
    except Exception as e:
        print(f"Error fetching tags for {artist_name}: {e}")
        return []

def main():
    print("Starting PUNK-SCOUT City-Scraper Engine...")

    # 1. Update Linktrees for High Priority Artists (Keep this logic)
    core_artists = get_core_artists()
    print(f"Checking Linktrees for {len(core_artists)} core artists...")
    for artist in core_artists:
        linktree_sniffer(artist)
        update_artist_last_checked(artist['id'])

    # 2. Songkick City Scraper
    locations = get_locations()

    # Priority Scan logic:
    # Priority 3: Every run
    # Priority 2: Every run (assuming they are intermediate)
    # Priority 1: Only on weekends (Saturday=5, Sunday=6)
    today_weekday = datetime.now().weekday()
    is_weekend = today_weekday >= 5

    filtered_locations = []
    for loc in locations:
        p = loc.get('scan_priority', 3)
        if p >= 2:
            filtered_locations.append(loc)
        elif p == 1 and is_weekend:
            filtered_locations.append(loc)
        else:
            print(f"Skipping {loc['city']} (Priority {p}, Weekday {today_weekday})")

    print(f"Scanning {len(filtered_locations)} locations...")

    known_artists = get_all_artists_names()

    punk_keywords = ['punk', 'hardcore', 'ska', 'oi']

    for loc in filtered_locations:
        city_id = loc['songkick_id']
        country = loc['country']
        city_name = loc['city']

        print(f"\n--- Scraping {city_name}, {country} ---")
        scraped_events = scrape_songkick_city(city_id, country, city_name)
        print(f"Found {len(scraped_events)} events.")

        for event in scraped_events:
            artist_name = event['artist']

            # Requirement: The 'Golden Match' logic
            if artist_name in known_artists:
                print(f"  [MATCH] {artist_name} is in our watchlist.")
                # If it exists -> Mark as 'High Priority Event'
                event['priority'] = 'high'
                event['discovery_source'] = 'Core List'
                upsert_events([event], False)
            else:
                # Discovery logic for new artists
                print(f"  [NEW] Checking {artist_name} on Last.fm...")
                tags = get_artist_tags(artist_name)
                # Requirement: The Last.fm Filter (If the tags include punk, hardcore, ska, or oi)
                is_punk = any(kw in ' '.join(tags) for kw in punk_keywords)

                if is_punk:
                    print(f"    [DISCOVERED] {artist_name} is punk! (Tags: {', '.join(tags[:5])})")
                    # Requirement: Save the concert and add the artist to the database with is_core = false
                    try:
                        supabase.table("artists").insert({
                            "name": artist_name,
                            "is_core": False,
                            "is_discovered": True,
                            "genre_tags": tags
                        }).execute()
                        known_artists.add(artist_name)

                        event['priority'] = 'medium'
                        event['discovery_source'] = 'Songkick Scraper'
                        upsert_events([event], True)
                    except Exception as e:
                        print(f"    Error adding discovered artist {artist_name}: {e}")
                else:
                    print(f"    [SKIPPED] {artist_name} tags: {', '.join(tags[:5])}")

    print("\n--- SCOUT SUMMARY ---")
    print(f"Total events upserted: {summary['events_upserted']}")
    print(f"New Spain tours found: {summary['new_spain_tours']}")
    print(f"Proximity gigs detected: {summary['proximity_gigs']}")
    print(f"Linktree changes detected: {summary['linktree_changes']}")
    print("----------------------")

if __name__ == "__main__":
    main()
