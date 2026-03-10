import sys
from types import ModuleType

# 1. IMMEDIATE SSL MOCKING (Must be before any other imports)
# This handles the stlite/pyodide environment where the 'ssl' module is missing or crippled.
ssl_mock = ModuleType("ssl")
ssl_mock.PROTOCOL_TLS_CLIENT = 16
ssl_mock.CERT_NONE = 0
ssl_mock.CERT_REQUIRED = 2
ssl_mock.CERT_OPTIONAL = 1

class MockSSLContext:
    def __init__(self, protocol=None):
        self.verify_mode = ssl_mock.CERT_NONE
        self.check_hostname = False
    def load_verify_locations(self, *args, **kwargs): pass
    def set_default_verify_paths(self): pass
    def set_ciphers(self, ciphers): pass
    def wrap_socket(self, sock, **kwargs): return sock
    def set_alpn_protocols(self, protocols): pass

ssl_mock.SSLContext = MockSSLContext
sys.modules["ssl"] = ssl_mock

# 2. HTTPX PATCHING (To avoid 'h2' dependency and ensure it uses our mock)
import httpx
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

# Now import the rest of the dependencies
import streamlit as st
import pandas as pd
import os
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
