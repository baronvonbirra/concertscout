import streamlit as st
import pandas as pd
import os
import sys

# Mock ssl module for stlite/pyodide environment
try:
    import ssl
except ImportError:
    from types import ModuleType
    ssl = ModuleType("ssl")
    sys.modules["ssl"] = ssl

# Ensure ssl module has required attributes for httpx
if not hasattr(ssl, "PROTOCOL_TLS_CLIENT"):
    ssl.PROTOCOL_TLS_CLIENT = 16
if not hasattr(ssl, "CERT_NONE"):
    ssl.CERT_NONE = 0
if not hasattr(ssl, "CERT_REQUIRED"):
    ssl.CERT_REQUIRED = 2
if not hasattr(ssl, "CERT_OPTIONAL"):
    ssl.CERT_OPTIONAL = 1
if not hasattr(ssl, "OP_NO_COMPRESSION"):
    ssl.OP_NO_COMPRESSION = 0
if not hasattr(ssl, "OP_NO_SSLv2"):
    ssl.OP_NO_SSLv2 = 0
if not hasattr(ssl, "OP_NO_SSLv3"):
    ssl.OP_NO_SSLv3 = 0
if not hasattr(ssl, "OP_NO_TLSv1"):
    ssl.OP_NO_TLSv1 = 0
if not hasattr(ssl, "OP_NO_TLSv1_1"):
    ssl.OP_NO_TLSv1_1 = 0
if not hasattr(ssl, "OP_ALL"):
    ssl.OP_ALL = 0

if not hasattr(ssl, "HAS_ALPN"):
    ssl.HAS_ALPN = False

if not hasattr(ssl, "TLSVersion"):
    class TLSVersion:
        TLSv1_2 = 771
    ssl.TLSVersion = TLSVersion

if not hasattr(ssl, "SSLContext") or not hasattr(ssl.SSLContext, "options"):
    class SSLContext:
        options = 0
        def __init__(self, protocol=None):
            self.verify_mode = ssl.CERT_NONE
            self.check_hostname = False
            self.minimum_version = None
            self.options = 0
        def load_verify_locations(self, *args, **kwargs): pass
        def set_default_verify_paths(self): pass
        def set_ciphers(self, ciphers): pass
        def wrap_socket(self, sock, **kwargs): return sock
    ssl.SSLContext = SSLContext

# Patch httpx to use browser fetch API in stlite/pyodide
try:
    from pyodide_httpx import patch_httpx
    patch_httpx()
except ImportError:
    pass

import httpx

# Force http2=False for all httpx clients to avoid ImportError in stlite/pyodide
# where the 'h2' package is not available and browser fetch handles HTTP/2.
_orig_client_init = httpx.Client.__init__
def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("http2", None)
    kwargs["http2"] = False
    return _orig_client_init(self, *args, **kwargs)
httpx.Client.__init__ = _patched_client_init

_orig_async_client_init = httpx.AsyncClient.__init__
def _patched_async_client_init(self, *args, **kwargs):
    kwargs.pop("http2", None)
    kwargs["http2"] = False
    return _orig_async_client_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = _patched_async_client_init

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="PUNK-SCOUT V1.0", layout="wide")

# CUSTOM CSS FOR PUNK ZINE AESTHETIC
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Special+Elite&display=swap');

    /* Global Background and Text */
    .stApp {
        background-color: #000000;
        background-image: url("https://www.transparenttextures.com/patterns/carbon-fibre.png"); /* Subtle noise texture */
        color: #FFFFFF;
        font-family: 'Courier New', Courier, monospace;
    }

    /* Headers */
    h1, h2, h3 {
        color: #E60000 !important; /* Anarchy Red */
        font-family: 'Special Elite', cursive !important;
        text-transform: uppercase;
        letter-spacing: 2px;
    }

    /* Cards */
    .concert-card {
        border: 3px solid #FFFFFF;
        padding: 20px;
        margin-bottom: 20px;
        background-color: #000000;
    }

    /* Duct Tape Effect for Artist Name */
    .artist-name {
        background-color: #000000;
        color: #FFFFFF;
        border: 2px solid #FFFFFF;
        padding: 5px 15px;
        display: inline-block;
        font-weight: bold;
        transform: rotate(-2deg);
        font-size: 1.5rem;
        margin-bottom: 10px;
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
    }

    .ticket-btn:hover {
        background-color: #FFFFFF;
        color: #000000;
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
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    return None

supabase = get_supabase_client()

def fetch_events():
    if not supabase:
        return pd.DataFrame()
    response = supabase.table("events").select("*").order("date").execute()
    return pd.DataFrame(response.data)

def display_event_card(row):
    st.markdown(f"""
    <div class="concert-card">
        <div class="artist-name">{row['artist']}</div>
        <div style="font-size: 1.2rem; font-weight: bold; margin-bottom: 5px;">📍 {row['city']} - {row['venue']}</div>
        <div style="font-size: 1rem; color: #39FF14;">📅 {row['date']}</div>
        <a href="{row['ticket_url']}" target="_blank" class="ticket-btn">GET TICKETS</a>
    </div>
    """, unsafe_allow_html=True)

def main():
    st.title("PUNK-SCOUT V1.0")

    events_df = fetch_events()

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
                    display_event_card(row)
            else:
                st.write("EMPTY PIT.")

        with col2:
            st.header("THE DISTRO (DISTRO/RECS)")
            if not rec_events.empty:
                for _, row in rec_events.iterrows():
                    display_event_card(row)
            else:
                st.write("NO RECOMMENDATIONS.")

if __name__ == "__main__":
    main()
