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
import textwrap
import os
import json
from supabase import create_client, Client, ClientOptions
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="PUNK-SCOUT V2.0", layout="wide")

# Supabase Setup
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

@st.cache_resource
def get_supabase_client():
    if SUPABASE_URL and SUPABASE_KEY:
        return create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(realtime=None))
    return None

supabase = get_supabase_client()

def fetch_consolidated_data():
    if not supabase:
        return []

    try:
        # Fetch concerts joined with artist details
        res = supabase.table("concerts").select("*, artists(name, spotify_id, instagram_url, lastfm_url, source_playlist, is_active)").execute()
        concerts_list = res.data if res.data else []

        consolidated = []
        for concert in concerts_list:
            artist_data = concert.get("artists")
            if isinstance(artist_data, list):
                artist_data = artist_data[0] if len(artist_data) > 0 else None

            if not artist_data:
                artist_data = {}

            # Skip inactive artists
            if not artist_data.get("is_active", True):
                continue

            merged = {**concert}
            merged["artist"] = artist_data.get("name", "Unknown Artist")
            merged["spotify_id"] = artist_data.get("spotify_id")
            merged["instagram_url"] = artist_data.get("instagram_url")
            merged["lastfm_url"] = artist_data.get("lastfm_url")
            merged["source_playlist"] = artist_data.get("source_playlist", "Weekly Ingestion")

            # Map event_date to date for frontend compatibility
            merged["date"] = concert.get("event_date", "Unknown Date")

            # Legacy fields fallbacks
            merged["genre_tags"] = []
            merged["punk_score"] = 100
            merged["is_recommendation"] = False
            merged["is_core"] = (artist_data.get("source_playlist") != "Weekly Punk")

            consolidated.append(merged)

        return consolidated
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        return []

def main():
    # Inject CSS to hide Streamlit elements and remove padding for mobile visibility
    st.markdown("""
        <style>
            #MainMenu {visibility: hidden;}
            header {visibility: hidden;}
            footer {visibility: hidden;}
            .block-container {
                padding-top: 0rem;
                padding-bottom: 0rem;
                padding-left: 0rem;
                padding-right: 0rem;
            }
            iframe {
                height: 100dvh !important;
            }
        </style>
    """, unsafe_allow_html=True)

    data = fetch_consolidated_data()
    events_json = json.dumps(data).replace("</script>", "<\\/script>")

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
            showTop: false,
            events: window.concertData,
            isNew(createdAt) {
                if (!createdAt) return false;
                const created = new Date(createdAt);
                const now = new Date();
                const diff = (now - created) / (1000 * 60 * 60); // hours
                return diff <= 72;
            },
            get filteredEvents() {
                return this.events
                    .filter(e => {
                        const matchSearch = e.artist.toLowerCase().includes(this.search.toLowerCase()) ||
                                            e.venue.toLowerCase().includes(this.search.toLowerCase());
                        const matchCity = this.city === 'All' || e.city === this.city;
                        const matchDiscovery = this.discovery ? !e.is_core : e.is_core;
                        return matchSearch && matchCity && matchDiscovery;
                    })
                    .sort((a, b) => {
                        const newA = this.isNew(a.created_at);
                        const newB = this.isNew(b.created_at);
                        if (newA && !newB) return -1;
                        if (!newA && newB) return 1;
                        return new Date(a.date) - new Date(b.date);
                    });
            },
            get cities() {
                return ['All', ...new Set(this.events.map(e => e.city))].sort();
            }
        }"
        @scroll.window="showTop = (window.pageYOffset > 500)"
        class="p-4 md:p-8">

            <!-- Go Up Button -->
            <button x-show="showTop"
                    @click="window.scrollTo({top: 0, behavior: 'smooth'})"
                    x-transition
                    class="fixed bottom-8 right-8 z-50 bg-[#CCFF00] text-black border-4 border-black p-4 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-x-[-2px] hover:translate-y-[-2px] hover:shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] transition-all">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 10l7-7m0 0l7 7m-7-7v18" />
                </svg>
            </button>

            <!-- Header & Search -->
            <div class="mb-12">
                <h1 class="bebas text-5xl md:text-8xl mb-6 tracking-tighter">PUNK-SCOUT <span class="acid-lime">V2.0</span></h1>

                <div class="flex flex-col md:flex-row gap-4 mb-8">
                    <input type="text" x-model="search" placeholder="SEARCH AS YOU TYPE..."
                           class="w-full md:w-1/2 bg-white text-black border-4 border-black p-4 text-xl md:text-2xl mono focus:outline-none shadow-[4px_4px_0px_0px_rgba(204,255,0,1)]">

                    <div class="flex border-4 border-white overflow-hidden">
                        <button @click="discovery = false" :class="!discovery ? 'bg-white text-black' : 'text-white'"
                                class="flex-1 px-4 md:px-6 py-3 bebas text-xl md:text-2xl transition-all">CORE BANDS</button>
                        <button @click="discovery = true" :class="discovery ? 'bg-acid-lime text-black' : 'text-white'"
                                class="flex-1 px-4 md:px-6 py-3 bebas text-xl md:text-2xl transition-all border-l-4 border-white">WEEKLY REFRESH</button>
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
                    <div class="brutal-card p-6 flex flex-col justify-between"
                         :class="{
                            'safety-orange': event.is_core,
                            '!border-[#CCFF00] !border-8': isNew(event.created_at)
                         }">
                        <div>
                            <div class="flex justify-between items-start mb-4">
                                <div class="flex gap-2">
                                    <span x-show="isNew(event.created_at)" class="bg-[#CCFF00] text-black px-2 py-1 text-xs mono font-bold">NEW</span>
                                    <span x-show="event.is_core" class="bg-[#FF5733] text-white px-2 py-1 text-xs mono">CORE</span>
                                    <span x-show="!event.is_core" class="bg-black text-white px-2 py-1 text-xs mono">WEEKLY</span>
                                </div>

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
                        </div>

                        <div class="mt-6 flex flex-col gap-2">
                            <div class="flex gap-2 w-full">
                                <a :href="event.ticket_url || '#'" target="_blank"
                                   class="bg-black text-white px-4 py-2 bebas text-xl hover:bg-acid-lime hover:text-black transition-colors border-2 border-black flex-1 text-center">GET TICKETS</a>

                                <template x-if="event.spotify_id">
                                    <a :href="'https://open.spotify.com/artist/' + event.spotify_id" target="_blank"
                                       class="bg-[#1DB954] text-white px-4 py-2 font-bold hover:bg-white hover:text-black transition-colors border-2 border-black text-center">SPOTIFY</a>
                                </template>

                                <template x-if="event.instagram_url">
                                    <a :href="event.instagram_url" target="_blank"
                                       class="bg-[#E1306C] text-white px-4 py-2 font-bold hover:bg-white hover:text-black transition-colors border-2 border-black text-center">INSTAGRAM</a>
                                </template>
                            </div>
                            <span class="mono text-[10px] opacity-50 self-end" x-text="'ID: ' + event.id"></span>
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
    st.components.v1.html(html_template.replace("__CONCERT_DATA__", events_json), height=1000, scrolling=True)

if __name__ == "__main__":
    main()
