import requests
from bs4 import BeautifulSoup

def test_scrape(city_id, country, city_name):
    base_url = f"https://www.songkick.com/metro-areas/{city_id}-{country.lower()}-{city_name.lower().replace(' ', '-')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    print(f"URL: {base_url}")
    try:
        response = requests.get(base_url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        listings = soup.find_all('li', class_='event-listings-element')
        print(f"Found {len(listings)} listings")
        for li in listings[:3]:
            # Artist
            artist_tag = li.find('p', class_='artists')
            artist_name = artist_tag.find('strong').get_text(strip=True) if artist_tag and artist_tag.find('strong') else "Unknown"

            # Venue & City
            location_tag = li.find('p', class_='location')
            venue = "Unknown"
            city = "Unknown"
            if location_tag:
                venue_tag = location_tag.find('a', class_='venue-link')
                if venue_tag:
                    venue = venue_tag.get_text(strip=True)

                # The city is usually text after the venue link
                # Or in a span. Let's print the location_tag text.
                loc_text = location_tag.get_text(strip=True)
                print(f"Raw location text: {loc_text}")

            print(f"Artist: {artist_name}, Venue: {venue}")
            print("-" * 20)
    except Exception as e:
        print(f"Error: {e}")

test_scrape("28732", "spain", "madrid")
