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
from supabase import create_client, Client, ClientOptions
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="PUNK-SCOUT V2.0", layout="wide")

# CUSTOM CSS FOR PUNK ZINE AESTHETIC V2
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Special+Elite&display=swap');

    /* Global Background and Text */
    .stApp {
        background-color: #000000;
        background-image:
            url("https://www.transparenttextures.com/patterns/carbon-fibre.png"), /* Carbon fibre texture */
            url("https://www.transparenttextures.com/patterns/pinstriped-suit.png"); /* Xerox-like grain */
        color: #FFFFFF;
        font-family: 'Courier New', Courier, monospace;
    }

    /* Headers with Duct Tape effect */
    h1, h2, h3 {
        color: #E60000 !important; /* Anarchy Red */
        font-family: 'Special Elite', cursive !important;
        text-transform: uppercase;
        letter-spacing: 2px;
        background-color: #000000;
        display: inline-block;
        padding: 5px 20px;
        border: 4px solid #FFFFFF;
        box-shadow: 5px 5px 0px #E60000;
        margin-bottom: 20px;
    }

    /* Cards */
    .concert-card {
        border: 3px solid #FFFFFF;
        padding: 20px;
        margin-bottom: 20px;
        background-color: #000000;
        position: relative;
        overflow: hidden;
    }

    .concert-card::before {
        content: "";
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        background: url("https://www.transparenttextures.com/patterns/asfalt-dark.png");
        opacity: 0.2;
        pointer-events: none;
    }

    /* Duct Tape Effect for Artist Name */
    .artist-name {
        background-color: #FFFFFF;
        color: #000000;
        padding: 5px 15px;
        display: inline-block;
        font-weight: bold;
        transform: rotate(-1.5deg);
        font-size: 1.5rem;
        margin-bottom: 15px;
        box-shadow: 3px 3px 0px #39FF14;
    }

    /* Badges */
    .badge {
        padding: 2px 8px;
        font-weight: bold;
        text-transform: uppercase;
        font-size: 0.8rem;
        display: inline-block;
        margin-right: 5px;
        margin-bottom: 5px;
    }

    .badge-leak {
        background-color: #E60000;
        color: #FFFFFF;
        animation: blinker 1s linear infinite;
    }

    @keyframes blinker {
        50% { opacity: 0; }
    }

    .badge-proximity {
        background-color: #FFFF00;
        color: #000000;
    }

    /* Tickets Button */
    .ticket-btn {
        background-color: #39FF14; /* Radioactive Green */
        color: #000000;
        padding: 10px 20px;
        text-decoration: none;
        font-weight: bold;
        border: 2px solid #000000;
        display: inline-block;
        margin-top: 10px;
        box-shadow: 4px 4px 0px #FFFFFF;
    }

    .ticket-btn:hover {
        background-color: #FFFFFF;
        color: #000000;
        box-shadow: 4px 4px 0px #39FF14;
    }

    .source-footer {
        font-size: 0.7rem;
        color: #888888;
        margin-top: 15px;
        font-style: italic;
    }

    /* Layout Spacing */
    .stMarkdown {
        margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)

# Supabase Setup
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

@st.cache_resource
def get_supabase_client():
    if SUPABASE_URL and SUPABASE_KEY:
        return create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(realtime=None))
    return None

supabase = get_supabase_client()

def fetch_events():
    if not supabase:
        return pd.DataFrame()
    response = supabase.table("events").select("*").execute()
    return pd.DataFrame(response.data)

def fetch_artists():
    if not supabase:
        return pd.DataFrame()
    response = supabase.table("artists").select("name, last_linktree_snapshot, priority_level").execute()
    return pd.DataFrame(response.data)

def display_event_card(row, has_leak=False):
    badges = ""
    if has_leak:
        badges += '<span class="badge badge-leak">FAST-PASS / LEAK</span>'

    priority = str(row.get('priority', '')).lower()
    if priority == 'high':
        badges += '<span class="badge" style="background-color: #E60000; color: #FFFFFF;">RED ALERT</span>'
    elif priority == 'medium':
        badges += '<span class="badge" style="background-color: #39FF14; color: #000000;">DISCOVERY</span>'

    if row.get('is_proximity'):
        badges += '<span class="badge badge-proximity">BORDER CROSSER 🇵🇹/🇫🇷</span>'

    source_icon = "🎸" # Default
    if "Bandsintown" in str(row.get('source', '')):
        source_icon = "🎫"
    elif "Songkick" in str(row.get('source', '')):
        source_icon = "🤘"

    discovery_info = ""
    if row.get('discovery_source'):
        discovery_info = f'<div class="source-footer" style="margin-top: 0;">🔍 {row["discovery_source"]}</div>'

    st.markdown(textwrap.dedent(f"""
<div class="concert-card">
{badges}
<div class="artist-name">{row['artist']}</div>
<div style="font-size: 1.2rem; font-weight: bold; margin-bottom: 5px;">📍 {row['city']} - {row['venue']}</div>
<div style="font-size: 1rem; color: #39FF14;">📅 {row['date']}</div>
<a href="{row.get('ticket_url') or '#'}" target="_blank" class="ticket-btn">GET TICKETS</a>
<div class="source-footer">{source_icon} SOURCE: {row.get('source') or 'UNKNOWN'}</div>
{discovery_info}
</div>
""").strip(), unsafe_allow_html=True)

def main():
    st.title("PUNK-SCOUT V2.0")

    events_df = fetch_events()
    artists_df = fetch_artists()

    # Simple logic to identify "leaks": artists with high priority and linktree snapshots
    # (In a real scenario, we might track if the snapshot changed RECENTLY)
    # For now, let's say if priority is high, we show the badge as it's been "sniffed"
    leaky_artists = set()
    if not artists_df.empty:
        leaky_artists = set(artists_df[
            (artists_df['priority_level'].str.lower().isin(['high', '10', 'top']))
        ]['name'])

    if events_df.empty:
        st.write("NO TOURS FOUND YET. KEEP REBELLIOUS.")
    else:
        # Split into Core and Recommendations
        core_events = events_df[events_df['is_recommendation'] == False]
        rec_events = events_df[events_df['is_recommendation'] == True]

        col1, col2 = st.columns(2)

        with col1:
            st.header("THE PIT (CORE)")
            if not core_events.empty:
                for _, row in core_events.iterrows():
                    display_event_card(row, has_leak=row['artist'] in leaky_artists)
            else:
                st.write("EMPTY PIT.")

        with col2:
            st.header("THE DISTRO (DISTRO/RECS)")
            if not rec_events.empty:
                for _, row in rec_events.iterrows():
                    display_event_card(row, has_leak=row['artist'] in leaky_artists)
            else:
                st.write("NO RECOMMENDATIONS.")

if __name__ == "__main__":
    main()
