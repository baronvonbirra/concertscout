import requests
import re
import time

def get_city_name(id):
    url = f"https://www.songkick.com/metro-areas/{id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            match = re.search(r'<title>(.*?) - Songkick</title>', resp.text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    except:
        pass
    return None

# We'll check 28700 to 28800.
for i in range(28700, 28800):
    name = get_city_name(i)
    if name and "Spain" in name:
        print(f"{i}: {name}")
    time.sleep(0.5)
