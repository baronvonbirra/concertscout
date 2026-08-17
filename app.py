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
from datetime import datetime, timedelta
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

@st.cache_data(ttl=3600)
def fetch_consolidated_data():
    if not supabase:
        return {"concerts": [], "artists": [], "weekly_playlist": [], "tour_events": [], "weekly_submissions": []}

    try:
        # Fetch tour_events for Phase 3 display
        tour_events = []
        try:
            te_res = supabase.table("tour_events").select("*").order("event_date", desc=False).execute()
            tour_events = te_res.data if te_res.data else []
        except Exception as te_e:
            print(f"Error fetching tour_events: {te_e}")

        # Fetch weekly submissions for Phase 1 display
        weekly_submissions = []
        try:
            ws_res = supabase.table("weekly_submissions").select("*").order("created_at", desc=True).execute()
            weekly_submissions = ws_res.data if ws_res.data else []
        except Exception as ws_e:
            print(f"Error fetching weekly_submissions: {ws_e}")

        # Fetch concerts joined with artist details
        res = supabase.table("concerts").select("*, artists(id, name, spotify_id, instagram_url, lastfm_url, source_playlist, is_active, last_instagram_post_id)").execute()
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
            merged["artist_id"] = artist_data.get("id") or concert.get("artist_id")
            merged["spotify_id"] = artist_data.get("spotify_id")
            merged["instagram_url"] = artist_data.get("instagram_url")
            merged["lastfm_url"] = artist_data.get("lastfm_url")
            merged["source_playlist"] = artist_data.get("source_playlist", "Weekly Ingestion")
            merged["last_instagram_post_id"] = artist_data.get("last_instagram_post_id")

            # Map event_date to date for frontend compatibility
            merged["date"] = concert.get("event_date", "Unknown Date")

            # Legacy fields fallbacks
            merged["genre_tags"] = []
            merged["punk_score"] = 100
            merged["is_recommendation"] = False
            merged["is_core"] = (artist_data.get("source_playlist") != "Weekly Punk")

            consolidated.append(merged)

        # Fetch all active artists so users can scan their feeds/stories directly
        art_res = supabase.table("artists").select("id, name, spotify_id, instagram_url, source_playlist, last_instagram_post_id").eq("is_active", True).execute()
        active_artists = art_res.data if art_res.data else []

        # Fetch weekly playlist additions from playlist_history (added in last 7 days)
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()
        try:
            pl_res = supabase.table("playlist_history").select("*").gte("added_at", seven_days_ago).order("added_at", desc=True).execute()
            weekly_playlist = pl_res.data if pl_res.data else []
        except Exception as pe:
            print(f"Error fetching playlist history for UI: {pe}")
            weekly_playlist = []

        return {
            "concerts": consolidated,
            "artists": active_artists,
            "weekly_playlist": weekly_playlist,
            "tour_events": tour_events,
            "weekly_submissions": weekly_submissions
        }
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        return {"concerts": [], "artists": [], "weekly_playlist": [], "tour_events": [], "weekly_submissions": []}

def main():
    # Check if we should force refresh
    if st.query_params.get("refresh") == "true":
        st.cache_data.clear()
        st.query_params.pop("refresh", None)
        st.rerun()

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
        <script src="https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@400;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
        <style>
            [x-cloak] { display: none !important; }
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
            viewMode: 'tours',
            showTop: false,
            events: window.concertData.concerts || [],
            allArtists: window.concertData.artists || [],
            weeklyPlaylist: window.concertData.weekly_playlist || [],
            tourEvents: window.concertData.tour_events || [],
            weeklySubmissions: window.concertData.weekly_submissions || [],

            // Admin Panel State
            adminAuth: false,
            adminPasswordInput: '',
            adminError: '',
            adminSubmitting: false,
            adminSuccess: false,
            subWeek: 'W33',
            subBandName: '',
            subDirectViews: 0,
            subIndirectViews: 0,
            subTotalSaves: 0,
            subInteractionType: 'none',
            subNotes: '',
            bulkCsv: '',
            bulkMode: false,

            get currentScore() {
                const type = this.subInteractionType;
                if (type === 'liked+shared+thanked') return 3;
                if (type === 'liked+shared') return 2;
                if (['liked', 'shared', 'thanked'].includes(type)) return 1;
                return 0;
            },

            get currentShared() {
                return this.subInteractionType.includes('shared');
            },

            verifyAdminPassword() {
                if (this.adminPasswordInput.trim() === 'punk2026' || window.location.search.includes('pwd=punk2026')) {
                    this.adminAuth = true;
                    this.adminError = '';
                } else {
                    this.adminError = 'Invalid password!';
                }
            },

            calcScore(type) {
                if (type === 'liked+shared+thanked') return 3;
                if (type === 'liked+shared') return 2;
                if (['liked', 'shared', 'thanked'].includes(type)) return 1;
                return 0;
            },

            async submitSingle() {
                if (!this.subBandName.trim()) {
                    alert('Band name is required!');
                    return;
                }
                this.adminSubmitting = true;
                this.adminSuccess = false;

                const payload = {
                    week: this.subWeek,
                    band_name: this.subBandName.trim(),
                    direct_views: parseInt(this.subDirectViews) || 0,
                    indirect_views: parseInt(this.subIndirectViews) || 0,
                    total_saves: parseInt(this.subTotalSaves) || 0,
                    interaction_type: this.subInteractionType,
                    interaction_score: this.calcScore(this.subInteractionType),
                    shared: this.subInteractionType.includes('shared'),
                    notes: this.subNotes || null
                };

                try {
                    const res = await fetch('__SUPABASE_URL__/rest/v1/weekly_submissions', {
                        method: 'POST',
                        headers: {
                            'apikey': '__SUPABASE_KEY__',
                            'Authorization': 'Bearer __SUPABASE_KEY__',
                            'Content-Type': 'application/json',
                            'Prefer': 'return=representation'
                        },
                        body: JSON.stringify(payload)
                    });

                    if (!res.ok) throw new Error('Failed to submit');

                    // Upsert into band_registry
                    await fetch('__SUPABASE_URL__/rest/v1/band_registry', {
                        method: 'POST',
                        headers: {
                            'apikey': '__SUPABASE_KEY__',
                            'Authorization': 'Bearer __SUPABASE_KEY__',
                            'Content-Type': 'application/json',
                            'Prefer': 'resolution=merge-duplicates'
                        },
                        body: JSON.stringify({
                            band_name: this.subBandName.trim(),
                            last_used_in_playlist: this.subWeek
                        })
                    });

                    this.adminSuccess = true;
                    this.adminSubmitting = false;
                    this.subBandName = '';
                    this.subDirectViews = 0;
                    this.subIndirectViews = 0;
                    this.subTotalSaves = 0;
                    this.subNotes = '';
                    setTimeout(() => this.forceRefresh(), 1200);
                } catch (e) {
                    alert('Error submitting: ' + e.message);
                    this.adminSubmitting = false;
                }
            },

            async submitBulk() {
                if (!this.bulkCsv.trim()) {
                    alert('Bulk CSV text is required!');
                    return;
                }
                this.adminSubmitting = true;

                const lines = this.bulkCsv.trim().split('\n');
                const items = [];
                const bandRegistryItems = [];

                for (let line of lines) {
                    if (!line.trim() || line.startsWith('Week,')) continue;
                    const parts = line.split(',').map(p => p.trim());
                    if (parts.length < 2) continue;

                    const week = parts[0] || this.subWeek;
                    const band_name = parts[1];
                    const direct_views = parseInt(parts[2]) || 0;
                    const indirect_views = parseInt(parts[3]) || 0;
                    const total_saves = parseInt(parts[4]) || 0;
                    const interaction_type = parts[5] || 'none';
                    const notes = parts[6] || null;

                    items.push({
                        week,
                        band_name,
                        direct_views,
                        indirect_views,
                        total_saves,
                        interaction_type,
                        interaction_score: this.calcScore(interaction_type),
                        shared: interaction_type.includes('shared'),
                        notes
                    });

                    bandRegistryItems.push({
                        band_name,
                        last_used_in_playlist: week
                    });
                }

                if (items.length === 0) {
                    alert('No valid rows found to import.');
                    this.adminSubmitting = false;
                    return;
                }

                try {
                    const res = await fetch('__SUPABASE_URL__/rest/v1/weekly_submissions', {
                        method: 'POST',
                        headers: {
                            'apikey': '__SUPABASE_KEY__',
                            'Authorization': 'Bearer __SUPABASE_KEY__',
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(items)
                    });

                    if (!res.ok) throw new Error('Bulk insert failed');

                    // Upsert into band_registry
                    await fetch('__SUPABASE_URL__/rest/v1/band_registry', {
                        method: 'POST',
                        headers: {
                            'apikey': '__SUPABASE_KEY__',
                            'Authorization': 'Bearer __SUPABASE_KEY__',
                            'Content-Type': 'application/json',
                            'Prefer': 'resolution=merge-duplicates'
                        },
                        body: JSON.stringify(bandRegistryItems)
                    });

                    this.adminSuccess = true;
                    this.adminSubmitting = false;
                    this.bulkCsv = '';
                    setTimeout(() => this.forceRefresh(), 1200);
                } catch (e) {
                    alert('Error bulk saving: ' + e.message);
                    this.adminSubmitting = false;
                }
            },

            hasConcert(artistName) {
                if (!artistName) return false;
                return this.events.some(e => e.artist.toLowerCase() === artistName.toLowerCase()) ||
                       this.tourEvents.some(t => t.band_name.toLowerCase() === artistName.toLowerCase());
            },

            ocrOpen: false,
            ocrArtist: '',
            ocrArtistId: null,
            ocrArtistIG: '',
            ocrLatestPostId: '',
            ocrStatus: '',
            ocrLoading: false,
            ocrResultText: '',
            ocrMatchedKeywords: [],
            ocrImageUrl: '',
            ocrFile: null,
            ocrProgress: 0,

            formEventName: '',
            formCity: 'Madrid',
            formVenue: 'Wurlitzer Ballroom',
            formDate: '',
            formTicketUrl: '',
            formSaving: false,
            formSuccess: false,
            formError: '',

            isNew(createdAt) {
                if (!createdAt) return false;
                const created = new Date(createdAt);
                const now = new Date();
                const diff = (now - created) / (1000 * 60 * 60);
                return diff <= 72;
            },
            get filteredEvents() {
                return this.events
                    .filter(e => {
                        const matchSearch = e.artist.toLowerCase().includes(this.search.toLowerCase()) ||
                                            e.venue.toLowerCase().includes(this.search.toLowerCase());
                        const matchCity = this.city === 'All' || e.city === this.city;
                        const matchDiscovery = this.viewMode === 'refresh' ? !e.is_core : e.is_core;
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
            get filteredArtists() {
                return this.allArtists
                    .filter(a => {
                        return a.name.toLowerCase().includes(this.search.toLowerCase());
                    })
                    .sort((a, b) => a.name.localeCompare(b.name));
            },
            get cities() {
                return ['All', ...new Set(this.events.map(e => e.city))].sort();
            },
            openOcrModal(artistName, artistId, instagramUrl, latestPostId) {
                this.ocrArtist = artistName;
                this.ocrArtistId = artistId;
                this.ocrArtistIG = instagramUrl || '';
                this.ocrLatestPostId = latestPostId || '';
                this.ocrFile = null;
                this.ocrImageUrl = '';
                this.ocrResultText = '';
                this.ocrMatchedKeywords = [];
                this.ocrStatus = '';
                this.ocrProgress = 0;
                this.formEventName = '';
                this.formCity = 'Madrid';
                this.formVenue = 'Wurlitzer Ballroom';
                this.formDate = '';
                this.formTicketUrl = '';
                this.formSuccess = false;
                this.formError = '';
                this.ocrOpen = true;
            },
            handleFileSelect(evt) {
                const f = evt.target.files[0];
                if (f) {
                    this.ocrFile = f;
                    this.ocrImageUrl = '';
                    this.ocrResultText = '';
                    this.ocrStatus = 'File loaded: ' + f.name;
                }
            },
            async runOcr() {
                if (!this.ocrImageUrl && !this.ocrFile) {
                    alert('Please upload a flyer screenshot or paste an image URL first!');
                    return;
                }
                this.ocrLoading = true;
                this.ocrStatus = 'Spawning Tesseract worker...';
                this.ocrResultText = '';
                this.ocrMatchedKeywords = [];
                this.ocrProgress = 0;
                this.formSuccess = false;
                this.formError = '';

                try {
                    const worker = await Tesseract.createWorker('eng', 1, {
                        logger: m => {
                            if (m.status === 'recognizing text') {
                                this.ocrStatus = 'Extracting text...';
                                this.ocrProgress = Math.round(m.progress * 100);
                            } else {
                                this.ocrStatus = m.status;
                            }
                        }
                    });

                    const source = this.ocrFile || this.ocrImageUrl;
                    const { data: { text } } = await worker.recognize(source);
                    await worker.terminate();

                    this.ocrResultText = text;
                    this.ocrLoading = false;
                    this.ocrStatus = 'Scan complete!';

                    const textLower = text.toLowerCase();
                    const triggerWords = ['spain', 'madrid', 'barcelona', 'tour', 'gira', 'concert', 'concierto', 'portugal', 'lisbon', 'lisboa', 'porto', 'sevilla', 'malaga', 'jerez', 'granada', 'valencia', 'bilbao'];
                    this.ocrMatchedKeywords = triggerWords.filter(word => textLower.includes(word));

                    this.formEventName = this.ocrArtist + ' Tour 2026';
                    this.formTicketUrl = this.ocrArtistIG || '';

                    const cities = ['Madrid', 'Barcelona', 'Sevilla', 'Málaga', 'Jerez', 'Granada', 'Valencia', 'Bilbao', 'Lisbon', 'Porto'];
                    for (let c of cities) {
                        if (textLower.includes(c.toLowerCase()) || (c === 'Lisbon' && textLower.includes('lisboa'))) {
                            this.formCity = c;
                            break;
                        }
                    }
                } catch (err) {
                    console.error(err);
                    this.ocrLoading = false;
                    this.ocrStatus = 'Error during scanning.';
                    this.formError = 'Failed to scan image: ' + err.message;
                }
            },
            async saveConcert() {
                if (!this.formDate) {
                    alert('Please specify the date of the concert!');
                    return;
                }
                this.formSaving = true;
                this.formSuccess = false;
                this.formError = '';

                const payload = {
                    artist_id: this.ocrArtistId,
                    event_name: this.formEventName,
                    city: this.formCity,
                    venue: this.formVenue,
                    event_date: this.formDate,
                    ticket_url: this.formTicketUrl || null,
                    source: 'instagram_ocr'
                };

                try {
                    const url = '__SUPABASE_URL__' + '/rest/v1/concerts';
                    const res = await fetch(url, {
                        method: 'POST',
                        headers: {
                            'apikey': '__SUPABASE_KEY__',
                            'Authorization': 'Bearer ' + '__SUPABASE_KEY__',
                            'Content-Type': 'application/json',
                            'Prefer': 'return=representation'
                        },
                        body: JSON.stringify(payload)
                    });

                    if (!res.ok) {
                        const errData = await res.json();
                        throw new Error(errData.message || 'Database insert failed.');
                    }

                    this.formSuccess = true;
                    this.formSaving = false;
                    setTimeout(() => {
                        this.ocrOpen = false;
                        this.forceRefresh();
                    }, 1500);
                } catch (err) {
                    console.error(err);
                    this.formSaving = false;
                    this.formError = 'Error saving to database: ' + err.message;
                }
            },
            forceRefresh() {
                try {
                    window.parent.location.search = '?refresh=true';
                } catch (e) {
                    window.location.search = '?refresh=true';
                }
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
                           class="w-full md:w-3/12 bg-white text-black border-4 border-black p-4 text-xl md:text-2xl mono focus:outline-none shadow-[4px_4px_0px_0px_rgba(204,255,0,1)]">

                    <div class="flex flex-wrap border-4 border-white overflow-hidden w-full md:w-7/12">
                        <button @click="viewMode = 'tours'" :class="viewMode === 'tours' ? 'bg-[#CCFF00] text-black' : 'text-white'"
                                class="flex-1 px-2 md:px-4 py-3 bebas text-lg md:text-xl transition-all">🎸 UPCOMING TOURS</button>
                        <button @click="viewMode = 'playlist'" :class="viewMode === 'playlist' ? 'bg-[#FFCC00] text-black' : 'text-white'"
                                class="flex-1 px-2 md:px-4 py-3 bebas text-lg md:text-xl transition-all border-l-2 md:border-l-4 border-white">PUNK IN PROGRESS</button>
                        <button @click="viewMode = 'admin'" :class="viewMode === 'admin' ? 'bg-[#FF5733] text-black' : 'text-white'"
                                class="flex-1 px-2 md:px-4 py-3 bebas text-lg md:text-xl transition-all border-l-2 md:border-l-4 border-white">ADMIN PANEL</button>
                        <button @click="viewMode = 'scan'" :class="viewMode === 'scan' ? 'bg-white text-black' : 'text-white'"
                                class="flex-1 px-2 md:px-4 py-3 bebas text-lg md:text-xl transition-all border-l-2 md:border-l-4 border-white">SCAN POSTERS</button>
                    </div>

                    <button @click="forceRefresh()"
                            class="w-full md:w-2/12 bg-acid-lime text-black border-4 border-black p-4 text-xl md:text-2xl bebas hover:translate-x-[-2px] hover:translate-y-[-2px] shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] transition-all">
                        REFRESH
                    </button>
                </div>

                <!-- City Pills -->
                <div class="flex flex-wrap gap-2" x-show="viewMode !== 'scan' && viewMode !== 'playlist'">
                    <template x-for="c in cities" :key="c">
                        <div @click="city = c" :class="city === c ? 'active' : ''"
                             class="pill mono text-sm uppercase" x-text="c"></div>
                    </template>
                </div>
            </div>

            <!-- Upcoming Tours Section (Phase 3) -->
            <div x-show="viewMode === 'tours'">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                    <template x-for="tour in tourEvents.filter(t => (city === 'All' || t.city === city) && (t.band_name.toLowerCase().includes(search.toLowerCase()) || t.venue.toLowerCase().includes(search.toLowerCase())))" :key="tour.id">
                        <div class="brutal-card p-6 flex flex-col justify-between border-4 border-black bg-white text-black shadow-[8px_8px_0px_0px_rgba(0,0,0,1)]">
                            <div>
                                <div class="flex justify-between items-start mb-4">
                                    <span class="bg-[#CCFF00] text-black px-2 py-1 text-xs mono font-bold border border-black uppercase" x-text="tour.country"></span>
                                    <span class="mono text-xs opacity-60" x-text="tour.source || 'last.fm'"></span>
                                </div>

                                <h2 class="bebas text-4xl mb-2 leading-none" x-text="tour.band_name"></h2>
                                <div class="mono text-sm mb-4 uppercase">
                                    <span class="font-bold" x-text="tour.city"></span> @ <span x-text="tour.venue"></span>
                                </div>

                                <div class="bg-black text-white inline-block px-3 py-1 mono text-lg mb-4" x-text="tour.event_date"></div>
                            </div>

                            <div class="mt-6 flex flex-col gap-2">
                                <a :href="tour.last_fm_url || '#'" target="_blank"
                                   class="bg-black text-white px-4 py-2 bebas text-xl hover:bg-[#CCFF00] hover:text-black transition-colors border-2 border-black text-center uppercase">GET TICKETS</a>
                            </div>
                        </div>
                    </template>
                </div>

                <!-- Empty State for Tours -->
                <div x-show="tourEvents.length === 0" class="text-center py-20">
                    <p class="bebas text-4xl opacity-50">NO UPCOMING TOURS IN SPAIN / PORTUGAL. STAY REBELLIOUS.</p>
                </div>
            </div>

            <!-- Admin Panel Section (Phase 1) -->
            <div x-show="viewMode === 'admin'" x-cloak>
                <div class="mb-8 bg-[#FF5733] border-4 border-black p-6 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] text-black">
                    <h2 class="bebas text-4xl mb-2 tracking-wide uppercase">⚡ DIY PUNK ADMIN DASHBOARD ⚡</h2>
                    <p class="mono text-sm leading-relaxed">
                        Submit weekly band performance data. Pre-calculated interaction scores & derive derived flags with zero human error.
                    </p>
                </div>

                <!-- Password Check Panel -->
                <div x-show="!adminAuth" class="brutal-card p-8 bg-white text-black max-w-md mx-auto">
                    <h3 class="bebas text-3xl mb-4 uppercase">ENTER PASSWORD</h3>
                    <input type="password" x-model="adminPasswordInput" @keydown.enter="verifyAdminPassword()" placeholder="Password..."
                           class="w-full bg-white border-4 border-black p-3 text-lg mono focus:outline-none mb-4">
                    <button @click="verifyAdminPassword()"
                            class="w-full bg-[#CCFF00] text-black font-bold border-4 border-black p-3 text-xl bebas tracking-wide hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all uppercase">
                        LOGIN
                    </button>
                    <p x-show="adminError" class="text-red-600 font-bold mono text-sm mt-3" x-text="adminError"></p>
                </div>

                <!-- Authenticated Admin Forms -->
                <div x-show="adminAuth" class="space-y-8">
                    <!-- Toggle Single vs Bulk -->
                    <div class="flex gap-4">
                        <button @click="bulkMode = false" :class="!bulkMode ? 'bg-[#CCFF00] text-black' : 'bg-black text-white'" class="px-6 py-2 bebas text-2xl border-4 border-black">SINGLE SUBMISSION</button>
                        <button @click="bulkMode = true" :class="bulkMode ? 'bg-[#CCFF00] text-black' : 'bg-black text-white'" class="px-6 py-2 bebas text-2xl border-4 border-black">BULK CSV IMPORT</button>
                    </div>

                    <!-- Single Submission Form -->
                    <div x-show="!bulkMode" class="brutal-card p-6 bg-white text-black">
                        <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase">WEEKLY SUBMISSION FORM</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block font-bold text-xs uppercase mb-1">WEEK (e.g. W33):</label>
                                <input type="text" x-model="subWeek" class="w-full border-2 border-black p-2 mono text-sm">
                            </div>
                            <div>
                                <label class="block font-bold text-xs uppercase mb-1">BAND NAME:</label>
                                <input type="text" x-model="subBandName" class="w-full border-2 border-black p-2 mono text-sm">
                            </div>
                            <div>
                                <label class="block font-bold text-xs uppercase mb-1">DIRECT VIEWS:</label>
                                <input type="number" x-model="subDirectViews" class="w-full border-2 border-black p-2 mono text-sm">
                            </div>
                            <div>
                                <label class="block font-bold text-xs uppercase mb-1">INDIRECT VIEWS:</label>
                                <input type="number" x-model="subIndirectViews" class="w-full border-2 border-black p-2 mono text-sm">
                            </div>
                            <div>
                                <label class="block font-bold text-xs uppercase mb-1">TOTAL SAVES:</label>
                                <input type="number" x-model="subTotalSaves" class="w-full border-2 border-black p-2 mono text-sm">
                            </div>
                            <div>
                                <label class="block font-bold text-xs uppercase mb-1">INTERACTION TYPE:</label>
                                <select x-model="subInteractionType" class="w-full border-2 border-black p-2 mono text-sm font-bold">
                                    <option value="none">none (Score: 0)</option>
                                    <option value="liked">liked (Score: 1)</option>
                                    <option value="shared">shared (Score: 1)</option>
                                    <option value="liked+shared">liked+shared (Score: 2)</option>
                                    <option value="thanked">thanked (Score: 1)</option>
                                    <option value="liked+shared+thanked">liked+shared+thanked (Score: 3)</option>
                                </select>
                            </div>
                            <div class="md:col-span-2">
                                <label class="block font-bold text-xs uppercase mb-1">NOTES (OPTIONAL):</label>
                                <input type="text" x-model="subNotes" placeholder="e.g. repeat/special case..." class="w-full border-2 border-black p-2 mono text-sm">
                            </div>
                        </div>

                        <!-- Score Preview -->
                        <div class="mt-4 p-3 bg-gray-100 border-2 border-black flex justify-between items-center mono text-sm">
                            <span>AUTO-CALCULATED SCORE: <strong x-text="currentScore"></strong></span>
                            <span>DERIVED SHARED: <strong x-text="currentShared ? 'YES' : 'NO'"></strong></span>
                        </div>

                        <button @click="submitSingle()" :disabled="adminSubmitting"
                                class="w-full bg-[#CCFF00] text-black font-bold border-4 border-black p-4 mt-6 text-xl bebas tracking-wider hover:translate-x-[-2px] hover:translate-y-[-2px] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all uppercase">
                            SAVE SUBMISSION
                        </button>
                    </div>

                    <!-- Bulk Import Form -->
                    <div x-show="bulkMode" class="brutal-card p-6 bg-white text-black">
                        <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase">BULK CSV PASTE IMPORT</h3>
                        <p class="mono text-xs mb-2">Paste CSV rows in format: <code>Week,Band_Name,Direct_Views,Indirect_Views,Total_Saves,Interaction_Type,Notes</code></p>
                        <textarea x-model="bulkCsv" rows="6" placeholder="W33, Band Name, 100, 50, 10, liked+shared, Note text..." class="w-full border-2 border-black p-3 mono text-xs focus:outline-none"></textarea>
                        <button @click="submitBulk()" :disabled="adminSubmitting"
                                class="w-full bg-[#CCFF00] text-black font-bold border-4 border-black p-4 mt-4 text-xl bebas tracking-wider hover:translate-x-[-2px] hover:translate-y-[-2px] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all uppercase">
                            PROCESS BULK IMPORT
                        </button>
                    </div>

                    <p x-show="adminSuccess" class="p-3 bg-green-200 border-2 border-green-800 text-green-900 font-bold mono text-center">Success! Data saved.</p>

                    <!-- Submission Log Table -->
                    <div class="brutal-card p-6 bg-white text-black overflow-x-auto">
                        <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase">RECENT SUBMISSIONS LOG</h3>
                        <table class="w-full text-left border-collapse mono text-xs">
                            <thead>
                                <tr class="bg-black text-white">
                                    <th class="p-2 border border-black">WEEK</th>
                                    <th class="p-2 border border-black">BAND</th>
                                    <th class="p-2 border border-black">DIRECT</th>
                                    <th class="p-2 border border-black">INDIRECT</th>
                                    <th class="p-2 border border-black">SAVES</th>
                                    <th class="p-2 border border-black">TYPE</th>
                                    <th class="p-2 border border-black">SCORE</th>
                                    <th class="p-2 border border-black">SHARED</th>
                                </tr>
                            </thead>
                            <tbody>
                                <template x-for="sub in weeklySubmissions" :key="sub.id">
                                    <tr class="hover:bg-yellow-50">
                                        <td class="p-2 border border-black font-bold" x-text="sub.week"></td>
                                        <td class="p-2 border border-black" x-text="sub.band_name"></td>
                                        <td class="p-2 border border-black" x-text="sub.direct_views"></td>
                                        <td class="p-2 border border-black" x-text="sub.indirect_views"></td>
                                        <td class="p-2 border border-black" x-text="sub.total_saves"></td>
                                        <td class="p-2 border border-black" x-text="sub.interaction_type"></td>
                                        <td class="p-2 border border-black font-bold" x-text="sub.interaction_score"></td>
                                        <td class="p-2 border border-black" x-text="sub.shared ? 'YES' : 'NO'"></td>
                                    </tr>
                                </template>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Playlist View (Punk in Progress) -->
            <div x-show="viewMode === 'playlist'" x-cloak class="space-y-8">
                <!-- Explainer Card -->
                <div class="bg-[#FFCC00] border-4 border-black p-6 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] text-black">
                    <h2 class="bebas text-4xl mb-2 tracking-wide uppercase">🎸 PUNK IN PROGRESS 🎸</h2>
                    <p class="mono text-sm leading-relaxed">
                        Weekly emerging and established punk releases curation. Updated every Monday morning with exactly 10 fresh tracks. Sub-12 weeks tracks automatically pruned.
                    </p>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
                    <!-- Embedded Spotify Player -->
                    <div class="lg:col-span-5 flex flex-col">
                        <div class="brutal-card bg-black p-2 flex-grow h-[450px]">
                            <iframe src="https://open.spotify.com/embed/playlist/2ZqhNVOPmA3Nf0SRpzJ9Yz?utm_source=generator" width="100%" height="100%" frameBorder="0" allowfullscreen="" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy" style="border-radius: 0px; min-height: 400px;"></iframe>
                        </div>
                    </div>

                    <!-- Weekly Additions Details -->
                    <div class="lg:col-span-7">
                        <div class="brutal-card p-6 bg-white text-black">
                            <h3 class="bebas text-3xl mb-4 uppercase tracking-tight border-b-4 border-black pb-2">🆕 RECENT WEEKLY ADDITIONS</h3>

                            <div class="space-y-4 max-h-[500px] overflow-y-auto pr-2">
                                <template x-for="track in weeklyPlaylist" :key="track.id">
                                    <div class="border-2 border-black p-4 bg-gray-50 flex flex-col md:flex-row justify-between items-start md:items-center shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-x-[-1px] hover:translate-y-[-1px] hover:shadow-[5px_5px_0px_0px_rgba(0,0,0,1)] transition-all">
                                        <div>
                                            <h4 class="bebas text-2xl leading-tight" x-text="track.track_name"></h4>
                                            <p class="mono text-xs uppercase text-gray-600 mt-1">
                                                By <span class="font-bold text-black" x-text="track.artist_name"></span>
                                                &middot; Tier: <span class="font-bold" x-text="track.tier"></span>
                                                &middot; Monthly Listeners: <span x-text="track.monthly_listeners.toLocaleString()"></span>
                                            </p>

                                            <!-- Highlighting if Artist has active tours -->
                                            <template x-if="hasConcert(track.artist_name)">
                                                <div class="bg-[#CCFF00] text-black font-bold text-[10px] px-2 py-0.5 border border-black uppercase mt-2 inline-block shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                                                    ⚡ TOURING SPAIN/PORTUGAL
                                                </div>
                                            </template>
                                        </div>
                                        <div class="mt-4 md:mt-0 flex gap-2">
                                            <a :href="'https://open.spotify.com/track/' + track.track_id" target="_blank"
                                               class="bg-black text-white px-3 py-1.5 bebas text-sm tracking-wide border-2 border-black hover:bg-[#CCFF00] hover:text-black transition-colors uppercase">Listen</a>
                                            <a :href="'https://open.spotify.com/artist/' + track.artist_id" target="_blank"
                                               class="bg-white text-black px-3 py-1.5 bebas text-sm tracking-wide border-2 border-black hover:bg-black hover:text-white transition-colors uppercase">Artist</a>
                                        </div>
                                    </div>
                                </template>

                                <template x-if="weeklyPlaylist.length === 0">
                                    <div class="text-center py-10">
                                        <p class="mono text-sm text-gray-500 uppercase font-bold">NO ADDITIONS RECORDED IN THE LAST 7 DAYS.</p>
                                    </div>
                                </template>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Active Bands Grid for Direct In-Browser Scanning -->
            <div x-show="viewMode === 'scan'" x-cloak>
                <div class="mb-8 bg-[#FF5733] border-4 border-black p-6 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] text-black">
                    <h2 class="bebas text-4xl mb-2 tracking-wide uppercase">⚡ CROWD-SOURCED TOUR DISCOVERY ⚡</h2>
                    <p class="mono text-sm leading-relaxed">
                        Bands post their tour flyers on Instagram months before the ticketing sites list them. Select any of our active bands below to scan their IG profile feed, story, or a saved screenshot, and add new concerts directly to the database!
                    </p>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                    <template x-for="artist in filteredArtists" :key="artist.id">
                        <div class="brutal-card p-4 bg-white text-black flex flex-col justify-between">
                            <div>
                                <h3 class="bebas text-3xl mb-1 leading-none" x-text="artist.name"></h3>
                                <p class="mono text-[10px] uppercase tracking-wider text-gray-500 mb-4" x-text="'Source: ' + (artist.source_playlist || 'Unknown')"></p>
                            </div>
                            <div class="flex flex-col gap-2 mt-4">
                                <button @click="openOcrModal(artist.name, artist.id, artist.instagram_url, artist.last_instagram_post_id)"
                                        class="w-full bg-[#CCFF00] text-black hover:bg-black hover:text-white px-3 py-2 border-2 border-black font-bold text-xs tracking-tight transition-all uppercase">
                                    Scan IG Story/Feed
                                </button>
                                <div class="flex gap-2">
                                    <template x-if="artist.spotify_id">
                                        <a :href="'https://open.spotify.com/artist/' + artist.spotify_id" target="_blank"
                                           class="flex-1 bg-[#1DB954] text-white text-center text-xs py-1 font-bold border border-black hover:bg-white hover:text-black transition-colors uppercase">Spotify</a>
                                    </template>
                                    <template x-if="artist.instagram_url">
                                        <a :href="artist.instagram_url" target="_blank"
                                           class="flex-1 bg-[#E1306C] text-white text-center text-xs py-1 font-bold border border-black hover:bg-white hover:text-black transition-colors uppercase">Instagram</a>
                                    </template>
                                </div>
                            </div>
                        </div>
                    </template>
                </div>

                <!-- Empty State -->
                <div x-show="filteredArtists.length === 0" class="text-center py-20">
                    <p class="bebas text-4xl opacity-50">NO BANDS MATCHING YOUR SEARCH.</p>
                </div>
            </div>

            <!-- NEO-BRUTALIST INSTAGRAM TOUR FLYER SCANNER MODAL -->
            <div x-show="ocrOpen" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 overflow-y-auto" x-cloak>
                <div class="brutal-card bg-white text-black p-6 w-full max-w-2xl relative my-8" @click.away="ocrOpen = false">
                    <!-- Close button -->
                    <button @click="ocrOpen = false" class="absolute top-4 right-4 font-bold text-2xl hover:text-[#FF5733] transition-colors">&times;</button>

                    <h2 class="bebas text-4xl mb-1 tracking-wide uppercase">TOUR POSTER SCANNER</h2>
                    <p class="mono text-xs text-gray-500 mb-6 uppercase" x-text="'Act: ' + ocrArtist"></p>

                    <!-- Drag and Drop / File Input -->
                    <div class="border-4 border-dashed border-black p-6 bg-yellow-50 hover:bg-yellow-100 transition-colors flex flex-col items-center justify-center font-bold text-center relative">
                        <input type="file" @change="handleFileSelect" class="absolute inset-0 opacity-0 cursor-pointer" accept="image/*">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-10 w-10 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                        </svg>
                        <span x-text="ocrFile ? 'Uploaded: ' + ocrFile.name : 'UPLOAD IG STORY SCREENSHOT / FLYER IMAGE'"></span>
                        <span class="text-[10px] opacity-60 font-normal mt-1">Accepts PNG, JPG, JPEG (drag and drop here)</span>
                    </div>

                    <!-- Automated Latest Instagram Post Detection & One-Click Auto-Fill -->
                    <template x-if="ocrLatestPostId">
                        <div class="mt-4 p-4 border-4 border-black bg-purple-50">
                            <p class="font-bold text-xs uppercase mb-2">📸 LATEST INSTAGRAM POST DETECTED:</p>
                            <div class="flex gap-4 items-start">
                                <img :src="'https://www.instagram.com/p/' + ocrLatestPostId + '/media/?size=m'"
                                     class="w-24 h-24 object-cover border-2 border-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]"
                                     alt="Latest IG post preview">
                                <div class="flex-1">
                                    <p class="text-xs font-mono mb-2">Shortcode: <span x-text="ocrLatestPostId" class="font-bold text-purple-700 text-xs"></span></p>
                                    <button @click="ocrImageUrl = 'https://www.instagram.com/p/' + ocrLatestPostId + '/media/?size=l'; ocrFile = null; ocrStatus = 'Latest post image URL auto-filled!';"
                                            class="bg-[#CCFF00] hover:bg-black hover:text-white text-black font-bold text-xs px-3 py-2 border-2 border-black uppercase transition-all shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                                        USE LATEST POST IMAGE
                                    </button>
                                </div>
                            </div>
                        </div>
                    </template>

                    <!-- OR URL Input -->
                    <div class="mt-4">
                        <label class="block font-bold text-xs uppercase mb-1">OR PASTE FLYER IMAGE URL:</label>
                        <input type="text" x-model="ocrImageUrl" @input="ocrFile = null; ocrResultText = ''; ocrStatus = ocrImageUrl ? 'URL set.' : ''" placeholder="https://example.com/tour-poster.jpg"
                               class="w-full bg-white border-4 border-black p-3 text-sm mono focus:outline-none">
                    </div>

                    <!-- DIY Suggestion helpers -->
                    <div class="mt-4 bg-gray-100 p-4 border-2 border-black">
                        <p class="font-bold text-xs mb-2 uppercase">🔍 DO NOT HAVE THE FLYER? FIND IT ONLINE:</p>
                        <div class="flex flex-wrap gap-2">
                            <a :href="'https://www.google.com/search?tbm=isch&q=' + encodeURIComponent(ocrArtist + ' tour poster flyer spain')" target="_blank"
                               class="bg-[#121212] text-white px-3 py-1 font-bold text-xs hover:bg-[#CCFF00] hover:text-black border border-black transition-colors uppercase">Google Images</a>
                            <a :href="ocrArtistIG || 'https://www.instagram.com/'" target="_blank"
                               class="bg-[#E1306C] text-white px-3 py-1 font-bold text-xs hover:bg-white hover:text-black border border-black transition-colors uppercase">Visit Instagram</a>
                            <a :href="'https://duckduckgo.com/?q=' + encodeURIComponent(ocrArtist + ' tour dates spain') + '&iax=images&ia=images'" target="_blank"
                               class="bg-[#FF5733] text-white px-3 py-1 font-bold text-xs hover:bg-white hover:text-black border border-black transition-colors uppercase">DuckDuckGo Images</a>
                        </div>
                    </div>

                    <!-- Run Scanner Button -->
                    <button @click="runOcr()" :disabled="ocrLoading"
                            class="w-full bg-[#CCFF00] text-black font-bold border-4 border-black p-4 mt-6 text-xl bebas tracking-wider hover:translate-x-[-2px] hover:translate-y-[-2px] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all uppercase flex justify-center items-center gap-2">
                        <span x-show="ocrLoading" class="animate-spin inline-block w-5 h-5 border-2 border-black border-t-transparent rounded-full"></span>
                        <span x-text="ocrLoading ? 'SCANNING PIXELS...' : 'RUN OCR SCAN'"></span>
                    </button>

                    <!-- Real-time scanner status -->
                    <template x-if="ocrStatus">
                        <div class="mt-4 p-3 bg-black text-white border-2 border-black mono text-xs">
                            <div class="flex justify-between items-center mb-1">
                                <span class="uppercase tracking-tight font-bold" x-text="ocrStatus"></span>
                                <span x-show="ocrLoading" x-text="ocrProgress + '%'"></span>
                            </div>
                            <div x-show="ocrLoading" class="w-full bg-gray-700 h-2 border border-white">
                                <div class="bg-[#CCFF00] h-full transition-all duration-300" :style="'width: ' + ocrProgress + '%'"></div>
                            </div>
                        </div>
                    </template>

                    <!-- Match Results Section -->
                    <template x-if="ocrResultText">
                        <div class="mt-6 border-4 border-black p-4 bg-yellow-50">
                            <h3 class="bebas text-2xl mb-2 tracking-wide uppercase">Matched Tour Keywords</h3>

                            <!-- Highlight Box -->
                            <div class="flex flex-wrap gap-2 mb-4">
                                <template x-for="word in ocrMatchedKeywords" :key="word">
                                    <span class="bg-[#FF5733] text-white font-bold mono text-xs px-2 py-1 border border-black uppercase" x-text="word"></span>
                                </template>
                                <template x-if="ocrMatchedKeywords.length === 0">
                                    <span class="bg-gray-200 text-gray-700 font-bold mono text-xs px-2 py-1 uppercase">No typical keywords found (Please review manually)</span>
                                </template>
                            </div>

                            <!-- Concert Save Form -->
                            <div class="bg-white border-2 border-black p-4">
                                <h4 class="bebas text-xl mb-3 tracking-wide uppercase">ONE-CLICK SAVE TO DATABASE</h4>

                                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label class="block font-bold text-xs uppercase mb-1">Event Name:</label>
                                        <input type="text" x-model="formEventName" class="w-full bg-white border-2 border-black p-2 mono text-xs">
                                    </div>
                                    <div>
                                        <label class="block font-bold text-xs uppercase mb-1">City:</label>
                                        <select x-model="formCity" class="w-full bg-white border-2 border-black p-2 mono text-xs font-bold uppercase">
                                            <option value="Madrid">Madrid</option>
                                            <option value="Barcelona">Barcelona</option>
                                            <option value="Málaga">Málaga</option>
                                            <option value="Jerez">Jerez</option>
                                            <option value="Sevilla">Sevilla</option>
                                            <option value="Granada">Granada</option>
                                            <option value="Valencia">Valencia</option>
                                            <option value="Bilbao">Bilbao</option>
                                            <option value="Lisbon">Lisbon</option>
                                            <option value="Porto">Porto</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label class="block font-bold text-xs uppercase mb-1">Venue:</label>
                                        <input type="text" x-model="formVenue" class="w-full bg-white border-2 border-black p-2 mono text-xs">
                                    </div>
                                    <div>
                                        <label class="block font-bold text-xs uppercase mb-1">Event Date:</label>
                                        <input type="date" x-model="formDate" class="w-full bg-white border-2 border-black p-2 mono text-xs font-bold">
                                    </div>
                                    <div class="md:col-span-2">
                                        <label class="block font-bold text-xs uppercase mb-1">Ticket / IG URL:</label>
                                        <input type="text" x-model="formTicketUrl" class="w-full bg-white border-2 border-black p-2 mono text-xs">
                                    </div>
                                </div>

                                <button @click="saveConcert()" :disabled="formSaving"
                                        class="w-full bg-[#FF5733] text-white font-bold border-2 border-black p-3 mt-4 text-sm hover:bg-[#CCFF00] hover:text-black transition-all uppercase flex justify-center items-center gap-2">
                                    <span x-show="formSaving" class="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full"></span>
                                    <span x-text="formSaving ? 'SAVING CONCERT...' : 'INSERT CONCERT TO DATABASE'"></span>
                                </button>

                                <template x-if="formSuccess">
                                    <div class="mt-3 p-2 bg-green-200 text-green-900 font-bold border-2 border-green-900 mono text-center text-xs uppercase">
                                        🎉 Concert Saved successfully! Refreshing dashboard...
                                    </div>
                                </template>

                                <template x-if="formError">
                                    <div class="mt-3 p-2 bg-red-200 text-red-900 font-bold border-2 border-red-900 mono text-center text-xs" x-text="formError"></div>
                                </template>
                            </div>

                            <!-- Raw OCR Text output (Accordion collapsible) -->
                            <div x-data="{ open: false }" class="mt-4 border border-black">
                                <button @click="open = !open" class="w-full bg-gray-200 p-2 text-left font-bold text-xs flex justify-between items-center">
                                    <span>COLLAPSED RAW OCR TEXT</span>
                                    <span x-text="open ? '▼' : '▶'"></span>
                                </button>
                                <div x-show="open" class="p-3 bg-white border-t border-black max-h-32 overflow-y-auto font-mono text-[10px] whitespace-pre-wrap" x-text="ocrResultText"></div>
                            </div>
                        </div>
                    </template>
                </div>
            </div>

        </div>
    </body>
    </html>
    """).strip()

    html_template = html_template.replace("__SUPABASE_URL__", SUPABASE_URL or "")
    html_template = html_template.replace("__SUPABASE_KEY__", SUPABASE_KEY or "")
    st.components.v1.html(html_template.replace("__CONCERT_DATA__", events_json), height=1000, scrolling=True)

if __name__ == "__main__":
    main()
