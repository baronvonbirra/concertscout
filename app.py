import sys
from types import ModuleType

# 1. COMPREHENSIVE SSL MOCK (MUST BE AT ABSOLUTE TOP)
try:
    import ssl
except ImportError:
    ssl = ModuleType("ssl")
    sys.modules["ssl"] = ssl

# Ensure ssl module has all required attributes for httpx and other libraries
ssl.PROTOCOL_TLS_CLIENT = 16
ssl.CERT_NONE = 0
ssl.CERT_REQUIRED = 2
ssl.CERT_OPTIONAL = 1
ssl.OP_NO_COMPRESSION = 0
ssl.OP_NO_SSLv2 = 0
ssl.OP_NO_SSLv3 = 0
ssl.OP_NO_TLSv1 = 0
ssl.OP_NO_TLSv1_1 = 0
ssl.OP_ALL = 0
ssl.HAS_ALPN = False
ssl.HAS_SNI = True

class TLSVersion:
    TLSv1_2 = 771
    TLSv1_3 = 772
    SSLv3 = 768
ssl.TLSVersion = TLSVersion

if not hasattr(ssl, "SSLContext") or not hasattr(ssl.SSLContext, "options"):
    class MockSSLContext:
        def __init__(self, protocol=None):
            self.verify_mode = ssl.CERT_NONE
            self.check_hostname = False
            self.minimum_version = None
            self.maximum_version = None
            self.options = 0
        def load_verify_locations(self, *args, **kwargs): pass
        def set_default_verify_paths(self): pass
        def set_ciphers(self, ciphers): pass
        def set_alpn_protocols(self, protocols): pass
        def wrap_socket(self, sock, **kwargs): return sock
    ssl.SSLContext = MockSSLContext

# 2. NETWORK PATCHING (BEFORE ANY OTHER IMPORTS)
try:
    import pyodide_http
    pyodide_http.patch_all()
except ImportError:
    pass


# 3. HTTPX SETUP & HTTP/2 DISABLE PATCH
import httpx

# Custom Transport for Pyodide using XMLHttpRequest (Synchronous)
class PyodideTransport(httpx.BaseTransport):
    def handle_request(self, request):
        try:
            from js import XMLHttpRequest, Uint8Array
        except ImportError:
            raise httpx.ConnectError("Pyodide (js) not found")

        try:
            xhr = XMLHttpRequest.new()
            # Synchronous mode (third param is False)
            xhr.open(request.method, str(request.url), False)

            # Set headers (filtering forbidden headers)
            forbidden_headers = {"host", "accept-encoding", "connection", "user-agent", "content-length", "expect", "referer", "origin"}
            for name, value in request.headers.items():
                if name.lower() not in forbidden_headers and not name.lower().startswith(("sec-", "proxy-")):
                    xhr.setRequestHeader(name, value)

            xhr.responseType = "arraybuffer"

            # Send body if present
            if request.content:
                # Use Uint8Array.from to convert bytes to JS-compatible format
                xhr.send(Uint8Array.from_(list(request.content)))
            else:
                xhr.send()

            # Parse headers
            resp_headers = []
            header_str = xhr.getAllResponseHeaders()
            if header_str:
                for line in header_str.strip().split('\r\n'):
                    if ':' in line:
                        k, v = line.split(':', 1)
                        k_lower = k.strip().lower()
                        # Browser/XHR automatically decompresses; remove these to prevent httpx from trying again
                        if k_lower not in ["content-encoding", "content-length", "transfer-encoding"]:
                            resp_headers.append((k.strip(), v.strip()))

            # Get content as bytes
            if xhr.response:
                resp_content = bytes(Uint8Array.new(xhr.response))
            else:
                resp_content = b""

            return httpx.Response(
                status_code=xhr.status,
                headers=resp_headers,
                content=resp_content,
                request=request
            )
        except Exception as e:
            raise httpx.ConnectError(str(e))

# Check if we are in Pyodide
IS_PYODIDE = "pyodide" in sys.modules or (hasattr(sys, "platform") and sys.platform == "emscripten")

_orig_client_init = httpx.Client.__init__
def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("http2", None)
    kwargs["http2"] = False
    if IS_PYODIDE and "transport" not in kwargs:
        kwargs["transport"] = PyodideTransport()
    return _orig_client_init(self, *args, **kwargs)
httpx.Client.__init__ = _patched_client_init

_orig_async_client_init = httpx.AsyncClient.__init__
def _patched_async_client_init(self, *args, **kwargs):
    kwargs.pop("http2", None)
    kwargs["http2"] = False
    return _orig_async_client_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = _patched_async_client_init

# 4. STANDARD IMPORTS
import streamlit as st
import pandas as pd
import textwrap
import os
import json
from supabase import create_client, Client, ClientOptions
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="PUNK-SCOUT NEO", layout="wide")

# Supabase Setup
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

@st.cache_resource
def get_supabase_client():
    if SUPABASE_URL and SUPABASE_KEY:
        return create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(realtime=None))
    return None

supabase = get_supabase_client()

@st.cache_data(ttl=3600)
def fetch_punk_keywords():
    if not supabase:
        return {'punk', 'hardcore', 'ska', 'oi', 'grindcore', 'crust'}
    try:
        res = supabase.table("keywords").select("word").eq("category", "punk").execute()
        if res.data:
            return {k['word'] for k in res.data}
    except Exception as e:
        print(f"Error fetching punk keywords: {e}")
    return {'punk', 'hardcore', 'ska', 'oi', 'grindcore', 'crust'}

def calculate_punk_score(tags, punk_keywords):
    if not tags: return 0
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except:
            tags = [tags]

    if not isinstance(tags, list): return 0

    punk_count = sum(1 for t in tags if any(pk in str(t).lower() for pk in punk_keywords))
    # Score based on top 5 tags primarily
    score = int((punk_count / min(len(tags), 5)) * 100)
    return min(score, 100)

def fetch_consolidated_data():
    if not supabase:
        return []

    try:
        # Fetch artists - restrict to verified, verified_auto or core bands
        # This ensures 'pending' artists (that haven't been processed by scout.py yet) don't show up if they should be blocked
        artists_res = (supabase.table("artists")
                       .select("name, is_core, genre_tags, status, priority_level")
                       .neq("status", "blocked")
                       .or_("status.in.(verified,verified_auto),is_core.eq.true")
                       .execute())
        artists_df = pd.DataFrame(artists_res.data)

        # Fetch events
        events_res = supabase.table("events").select("*").execute()
        events_df = pd.DataFrame(events_res.data)

        if events_df.empty:
            return []

        if artists_df.empty:
            # Should not happen if there are events, but safety first
            events_df['is_core'] = False
            events_df['punk_score'] = 0
            events_df['genre_tags'] = "[]"
            return events_df.to_dict('records')

        # Merge
        df = pd.merge(events_df, artists_df, left_on='artist', right_on='name', how='inner', suffixes=('', '_art'))

        # Fetch punk keywords for scoring
        punk_keywords = fetch_punk_keywords()

        # Calculate punk score
        df['punk_score'] = df['genre_tags'].apply(lambda x: calculate_punk_score(x, punk_keywords))

        # Format genre tags for JS
        df['genre_tags'] = df['genre_tags'].apply(lambda x: x if isinstance(x, list) else [])

        return df.to_dict('records')
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        return []

def main():
    data = fetch_consolidated_data()
    events_json = json.dumps(data)

    # NEO-BRUTALIST FRONTEND TEMPLATE
    html_template = textwrap.dedent("""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.tailwindcss.com"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@400;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
        <style>
            body {
                background-color: #121212;
                color: #FFFFFF;
                font-family: 'Inter', sans-serif;
            }
            .brutal-card {
                background-color: #FFFFFF;
                color: #000000;
                border: 4px solid #000000;
                box-shadow: 8px 8px 0px 0px rgba(0,0,0,1);
                transition: all 0.2s ease;
            }
            .brutal-card:hover {
                transform: translate(-2px, -2px);
                box-shadow: 10px 10px 0px 0px rgba(0,0,0,1);
            }
            .bebas { font-family: 'Bebas Neue', cursive; }
            .mono { font-family: 'JetBrains Mono', monospace; }
            .safety-orange { border-color: #FF5733 !important; }
            .acid-lime { color: #CCFF00; }
            .bg-acid-lime { background-color: #CCFF00; }
            .pill {
                border: 2px solid #FFFFFF;
                padding: 8px 16px;
                cursor: pointer;
                transition: all 0.2s;
            }
            .pill.active {
                background-color: #CCFF00;
                color: #000000;
                border-color: #CCFF00;
            }
        </style>
        <script>
            window.concertData = __CONCERT_DATA__;
        </script>
    </head>
    <body>
        <div x-data="{
            search: '',
            city: 'All',
            discovery: false,
            events: window.concertData,
            get filteredEvents() {
                return this.events.filter(e => {
                    const matchSearch = e.artist.toLowerCase().includes(this.search.toLowerCase()) ||
                                        e.venue.toLowerCase().includes(this.search.toLowerCase());
                    const matchCity = this.city === 'All' || e.city === this.city;
                    const matchDiscovery = this.discovery ? e.is_recommendation : !e.is_recommendation;
                    return matchSearch && matchCity && matchDiscovery;
                });
            },
            get cities() {
                return ['All', ...new Set(this.events.map(e => e.city))].sort();
            }
        }" class="p-4 md:p-8">

            <!-- Header & Search -->
            <div class="mb-12">
                <h1 class="bebas text-5xl md:text-8xl mb-6 tracking-tighter">PUNK-SCOUT <span class="acid-lime">NEO</span></h1>

                <div class="flex flex-col md:flex-row gap-4 mb-8">
                    <input type="text" x-model="search" placeholder="SEARCH AS YOU TYPE..."
                           class="w-full md:w-1/2 bg-white text-black border-4 border-black p-4 text-xl md:text-2xl mono focus:outline-none shadow-[4px_4px_0px_0px_rgba(204,255,0,1)]">

                    <div class="flex border-4 border-white overflow-hidden">
                        <button @click="discovery = false" :class="!discovery ? 'bg-white text-black' : 'text-white'"
                                class="flex-1 px-4 md:px-6 py-3 bebas text-xl md:text-2xl transition-all">MY BANDS</button>
                        <button @click="discovery = true" :class="discovery ? 'bg-acid-lime text-black' : 'text-white'"
                                class="flex-1 px-4 md:px-6 py-3 bebas text-xl md:text-2xl transition-all border-l-4 border-white">NEW DISCOVERIES</button>
                    </div>
                </div>

                <!-- City Pills -->
                <div class="flex flex-wrap gap-2">
                    <template x-for="c in cities" :key="c">
                        <div @click="city = c" :class="city === c ? 'active' : ''"
                             class="pill mono text-sm uppercase" x-text="c"></div>
                    </template>
                </div>
            </div>

            <!-- Grid -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                <template x-for="event in filteredEvents" :key="event.id">
                    <div class="brutal-card p-6 flex flex-col justify-between" :class="event.is_core ? 'safety-orange' : (event.is_recommendation ? 'border-dashed' : '')">
                        <div>
                            <div class="flex justify-between items-start mb-4">
                                <span x-show="event.is_recommendation" class="bg-black text-white px-2 py-1 text-xs mono" x-text="'SCORE: ' + event.punk_score + '%'"></span>
                                <span x-show="event.is_core" class="bg-[#FF5733] text-white px-2 py-1 text-xs mono">CORE</span>

                                <!-- Andalusia Map Mini-Thumb -->
                                <div class="w-12 h-12 relative">
                                    <template x-if="['Málaga', 'Jerez'].includes(event.city)">
                                        <svg viewBox="0 0 100 60" class="w-full h-full opacity-30">
                                            <path d="M5,45 L20,55 L80,55 L95,45 L95,15 L70,5 L20,5 L5,15 Z" fill="none" stroke="black" stroke-width="2" />
                                            <circle :cx="event.city === 'Málaga' ? 60 : 25" :cy="event.city === 'Málaga' ? 45 : 45" r="5" fill="#E60000" />
                                        </svg>
                                    </template>
                                    <template x-if="!['Málaga', 'Jerez'].includes(event.city)">
                                        <svg viewBox="0 0 24 24" class="w-full h-full opacity-20" fill="none" stroke="black" stroke-width="2">
                                            <circle cx="12" cy="12" r="10" />
                                            <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                                        </svg>
                                    </template>
                                </div>
                            </div>

                            <h2 class="bebas text-4xl mb-2 leading-none" x-text="event.artist"></h2>
                            <div class="mono text-sm mb-4 uppercase">
                                <span class="font-bold" x-text="event.city"></span> @ <span x-text="event.venue"></span>
                            </div>

                            <div class="bg-black text-white inline-block px-3 py-1 mono text-lg mb-4" x-text="event.date"></div>

                            <template x-if="event.is_recommendation && event.genre_tags && event.genre_tags.length > 0">
                                <div class="flex flex-wrap gap-1 mt-2">
                                    <template x-for="tag in event.genre_tags.slice(0,3)" :key="tag">
                                        <span class="text-[10px] mono border border-black px-1" x-text="tag"></span>
                                    </template>
                                </div>
                            </template>
                        </div>

                        <div class="mt-6 flex justify-between items-center">
                            <a :href="event.ticket_url || '#'" target="_blank"
                               class="bg-black text-white px-6 py-2 bebas text-xl hover:bg-acid-lime hover:text-black transition-colors">GET TICKETS</a>
                            <span class="mono text-[10px] opacity-50" x-text="'ID: ' + event.id"></span>
                        </div>
                    </div>
                </template>
            </div>

            <!-- Empty State -->
            <div x-show="filteredEvents.length === 0" class="text-center py-20">
                <p class="bebas text-4xl opacity-50">NO TOURS FOUND. KEEP REBELLIOUS.</p>
            </div>
        </div>
    </body>
    </html>
    """).strip()
    st.components.v1.html(html_template.replace("__CONCERT_DATA__", events_json), height=1200, scrolling=True)

if __name__ == "__main__":
    main()
