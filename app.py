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
try:
    from supabase import create_client, Client, ClientOptions
    HAS_SUPABASE_SDK = True
except ImportError:
    HAS_SUPABASE_SDK = False

from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="PUNK-SCOUT V2.0", layout="wide")

# Supabase Setup
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

class SimpleQueryBuilder:
    def __init__(self, url, headers, table_name):
        self.url = f"{url.rstrip('/')}/rest/v1/{table_name}"
        self.headers = headers
        self.params = {}

    def select(self, columns="*"):
        self.params["select"] = columns
        return self

    def order(self, column, desc=False):
        self.params["order"] = f"{column}.{'desc' if desc else 'asc'}"
        return self

    def limit(self, count):
        self.params["limit"] = str(count)
        return self

    def eq(self, column, value):
        val_str = "true" if value is True else ("false" if value is False else str(value))
        self.params[column] = f"eq.{val_str}"
        return self

    def gte(self, column, value):
        self.params[column] = f"gte.{value}"
        return self

    def execute(self):
        response = httpx.get(self.url, headers=self.headers, params=self.params, timeout=15.0)
        response.raise_for_status()
        class ResponseWrapper:
            def __init__(self, data):
                self.data = data
        return ResponseWrapper(response.json())

class SimpleSupabaseClient:
    def __init__(self, url, key):
        self.url = url
        self.key = key
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json"
        }

    def table(self, table_name):
        return SimpleQueryBuilder(self.url, self.headers, table_name)

@st.cache_resource
def get_supabase_client():
    if SUPABASE_URL and SUPABASE_KEY:
        if HAS_SUPABASE_SDK:
            try:
                return create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(realtime=None))
            except Exception:
                pass
        return SimpleSupabaseClient(SUPABASE_URL, SUPABASE_KEY)
    return None

supabase = get_supabase_client()

@st.cache_data(ttl=3600)
def fetch_consolidated_data():
    if not supabase:
        return {"concerts": [], "artists": [], "weekly_playlist": [], "tour_events": [], "weekly_submissions": [], "analytics_summary": [], "listener_snapshots": []}

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

        # Fetch analytics summary for Analytics Dashboard
        analytics_summary = []
        try:
            ans_res = supabase.table("band_analytics_summary").select("*").order("momentum_score", desc=True).execute()
            analytics_summary = ans_res.data if ans_res.data else []
        except Exception as ans_e:
            print(f"Error fetching band_analytics_summary: {ans_e}")

        # Fetch listener snapshots for details modal
        listener_snapshots = []
        try:
            snap_res = supabase.table("band_listener_snapshot").select("*").order("recorded_date", desc=True).limit(1000).execute()
            listener_snapshots = snap_res.data if snap_res.data else []
        except Exception as snap_e:
            print(f"Error fetching band_listener_snapshot: {snap_e}")

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
            "weekly_submissions": weekly_submissions,
            "analytics_summary": analytics_summary,
            "listener_snapshots": listener_snapshots
        }
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        return {"concerts": [], "artists": [], "weekly_playlist": [], "tour_events": [], "weekly_submissions": [], "analytics_summary": [], "listener_snapshots": []}

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
            analyticsSummary: window.concertData.analytics_summary || [],
            listenerSnapshots: window.concertData.listener_snapshots || [],

            // Analytics Dashboard State
            analyticsSortKey: 'momentum_score',
            analyticsSortAsc: false,
            analyticsFilter: 'all',
            analyticsSubTab: 'overview',
            selectedBandAnalytics: null,
            bandModalOpen: false,

            get filteredAnalytics() {
                return this.analyticsSummary
                    .filter(b => {
                        const matchSearch = b.band_name.toLowerCase().includes(this.search.toLowerCase());
                        if (!matchSearch) return false;
                        if (this.analyticsFilter === 'explosive') return b.growth_trajectory === 'explosive';
                        if (this.analyticsFilter === 'featured') return b.total_features > 0;
                        if (this.analyticsFilter === 'shared') return b.was_shared;
                        if (this.analyticsFilter === 'under100k') return b.latest_listener_count < 100000;
                        return true;
                    })
                    .sort((a, b) => {
                        let valA = a[this.analyticsSortKey];
                        let valB = b[this.analyticsSortKey];
                        if (typeof valA === 'string') valA = valA.toLowerCase();
                        if (typeof valB === 'string') valB = valB.toLowerCase();
                        if (valA < valB) return this.analyticsSortAsc ? -1 : 1;
                        if (valA > valB) return this.analyticsSortAsc ? 1 : -1;
                        return 0;
                    });
            },

            get totalBandsTracked() {
                return this.analyticsSummary.length;
            },

            get totalListeners() {
                const total = this.analyticsSummary.reduce((sum, b) => sum + (b.latest_listener_count || 0), 0);
                if (total >= 1000000) return (total / 1000000).toFixed(1) + 'M';
                if (total >= 1000) return (total / 1000).toFixed(1) + 'K';
                return total.toString();
            },

            get avgWowGrowth() {
                if (this.analyticsSummary.length === 0) return '+0.0%';
                const sum = this.analyticsSummary.reduce((acc, b) => acc + parseFloat(b.week_over_week_growth_pct || 0), 0);
                const avg = sum / this.analyticsSummary.length;
                return (avg >= 0 ? '+' : '') + avg.toFixed(1) + '%';
            },

            get mostExplosiveBand() {
                if (this.analyticsSummary.length === 0) return 'N/A';
                // Filter for acts with positive WoW growth (> 0) or explosive trajectory/momentum
                const candidates = [...this.analyticsSummary].filter(b => (b.week_over_week_growth_pct || 0) > 0 || b.growth_trajectory === 'explosive');
                if (candidates.length === 0) return 'N/A';
                candidates.sort((a, b) => {
                    const wowA = a.week_over_week_growth_pct || 0;
                    const wowB = b.week_over_week_growth_pct || 0;
                    if (wowA !== wowB) return wowB - wowA;
                    return (b.momentum_score || 0) - (a.momentum_score || 0);
                });
                return candidates[0] ? candidates[0].band_name + ' (+' + candidates[0].week_over_week_growth_pct + '%)' : 'N/A';
            },

            get totalBandsShared() {
                return this.analyticsSummary.filter(b => b.was_shared).length;
            },

            get avgSharesPerBand() {
                const sharedBands = this.analyticsSummary.filter(b => b.was_shared);
                if (sharedBands.length === 0) return '0.0';
                const totalShares = sharedBands.reduce((sum, b) => sum + (b.total_shares || 0), 0);
                return (totalShares / sharedBands.length).toFixed(1);
            },

            get mostSharedBand() {
                const sharedBands = [...this.analyticsSummary].filter(b => b.was_shared).sort((a, b) => (b.total_shares || 0) - (a.total_shares || 0));
                if (sharedBands.length === 0) return 'N/A';
                return sharedBands[0].band_name + ' (' + sharedBands[0].total_shares + 'x)';
            },

            get highImpactSharesCount() {
                return this.analyticsSummary.filter(b => b.was_shared && (b.avg_growth_after_share_pct || 0) > 10).length;
            },

            get shareCandidates() {
                return [...this.analyticsSummary]
                    .filter(b => !b.was_shared && (b.momentum_score || 0) > 70)
                    .sort((a, b) => (b.momentum_score || 0) - (a.momentum_score || 0));
            },

            get sharedBandsList() {
                return [...this.analyticsSummary]
                    .filter(b => b.was_shared)
                    .sort((a, b) => (b.total_shares || 0) - (a.total_shares || 0));
            },

            get highRoiSharesList() {
                return [...this.analyticsSummary]
                    .filter(b => b.was_shared && (b.avg_growth_after_share_pct || 0) > 10)
                    .sort((a, b) => (b.avg_growth_after_share_pct || 0) - (a.avg_growth_after_share_pct || 0));
            },

            getBandShareHistory(bandName) {
                if (!bandName || !bandName.trim()) return null;
                const match = this.analyticsSummary.find(b => b.band_name.toLowerCase() === bandName.trim().toLowerCase());
                if (!match) return null;
                return {
                    total_shares: match.total_shares || 0,
                    was_shared: match.was_shared || false,
                    last_shared_week: match.last_shared_week || 'N/A'
                };
            },

            getBandShareDecision(bandName) {
                if (!bandName || !bandName.trim()) {
                    return {
                        appearances: 0,
                        times_shared: 0,
                        success_rate_pct: 0,
                        total_saves: 0,
                        avg_interaction: 0,
                        share_pattern: 'never_shared',
                        recommendation: '🤔 NEUTRAL (Enter band name)',
                        tier: 'Tier 0: Neutral',
                        emoji: '🤔',
                        badge_bg: 'bg-gray-200',
                        badge_text: 'text-gray-800'
                    };
                }

                const nameLower = bandName.trim().toLowerCase();
                const bandSubs = this.weeklySubmissions.filter(s => s.band_name && s.band_name.toLowerCase() === nameLower);
                const appearances = bandSubs.length;
                const times_shared = bandSubs.filter(s => s.shared).length;
                const total_saves = bandSubs.reduce((sum, s) => sum + (s.total_saves || 0), 0);
                const total_interaction = bandSubs.reduce((sum, s) => sum + (s.interaction_score || 0), 0);
                const avg_interaction = appearances > 0 ? (total_interaction / appearances) : 0;
                const success_rate_pct = appearances > 0 ? Math.round((times_shared / appearances) * 100) : 0;

                // Also check analytics summary if available
                const summaryMatch = this.analyticsSummary.find(b => b.band_name && b.band_name.toLowerCase() === nameLower);

                let share_pattern = 'never_shared';
                if (times_shared === 0) {
                    share_pattern = 'never_shared';
                } else if (appearances > 0 && times_shared >= appearances * 0.8) {
                    share_pattern = 'always_share';
                } else if (appearances > 0 && times_shared >= appearances * 0.5) {
                    share_pattern = 'often_share';
                } else {
                    share_pattern = 'rarely_share';
                }

                let recommendation = '🤔 NEUTRAL';
                let tier = 'Tier 0: Neutral';
                let emoji = '🤔';
                let badge_bg = 'bg-gray-200';
                let badge_text = 'text-gray-900';

                if (share_pattern === 'always_share' || (times_shared > 0 && avg_interaction >= 1.8)) {
                    recommendation = '🚀 DEFINITE YES (proven)';
                    tier = 'Tier 1: Definite Yes';
                    emoji = '🚀';
                    badge_bg = 'bg-green-300';
                    badge_text = 'text-green-950';
                } else if (share_pattern === 'often_share' || (times_shared > 0 && total_saves > 80)) {
                    recommendation = '✅ LIKELY YES (track record)';
                    tier = 'Tier 2: Likely Yes';
                    emoji = '✅';
                    badge_bg = 'bg-lime-300';
                    badge_text = 'text-lime-950';
                } else if (share_pattern === 'never_shared' && total_saves > 100) {
                    recommendation = '⚡ MAYBE (unproven but hot)';
                    tier = 'Tier 3: Maybe';
                    emoji = '⚡';
                    badge_bg = 'bg-amber-300';
                    badge_text = 'text-amber-950';
                } else if (share_pattern === 'never_shared' && appearances >= 2) {
                    recommendation = '💭 MAYBE (let it cook more)';
                    tier = 'Tier 4: Let It Cook';
                    emoji = '💭';
                    badge_bg = 'bg-blue-200';
                    badge_text = 'text-blue-950';
                } else if (share_pattern === 'rarely_share') {
                    recommendation = '❌ SKIP (low track record)';
                    tier = 'Tier 5: Skip';
                    emoji = '❌';
                    badge_bg = 'bg-red-300';
                    badge_text = 'text-red-950';
                } else if (appearances === 0) {
                    recommendation = '✨ NEW BAND (first time feature)';
                    tier = 'Tier 0: New Band';
                    emoji = '✨';
                    badge_bg = 'bg-purple-200';
                    badge_text = 'text-purple-950';
                }

                return {
                    appearances,
                    times_shared,
                    success_rate_pct,
                    total_saves,
                    avg_interaction: avg_interaction.toFixed(2),
                    share_pattern,
                    recommendation,
                    tier,
                    emoji,
                    badge_bg,
                    badge_text
                };
            },

            openBandModal(band) {
                this.selectedBandAnalytics = band;
                this.bandModalOpen = true;
            },

            getBandSnapshots(bandName) {
                if (!bandName) return [];
                return this.listenerSnapshots
                    .filter(s => s.band_name.toLowerCase() === bandName.toLowerCase())
                    .sort((a, b) => new Date(b.recorded_date) - new Date(a.recorded_date));
            },

            getBandTours(bandName) {
                if (!bandName) return [];
                return this.tourEvents
                    .filter(t => t.band_name.toLowerCase() === bandName.toLowerCase())
                    .sort((a, b) => new Date(a.event_date) - new Date(b.event_date));
            },

            // Admin Panel State
            adminAuth: false,
            adminPasswordInput: '',
            adminError: '',
            adminSubmitting: false,
            adminSuccess: false,
            adminTab: 'submissions',
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

                const decision = this.getBandShareDecision(this.subBandName);

                const payload = {
                    week: this.subWeek,
                    band_name: this.subBandName.trim(),
                    direct_views: parseInt(this.subDirectViews) || 0,
                    indirect_views: parseInt(this.subIndirectViews) || 0,
                    total_saves: parseInt(this.subTotalSaves) || 0,
                    interaction_type: this.subInteractionType,
                    interaction_score: this.calcScore(this.subInteractionType),
                    shared: this.subInteractionType.includes('shared'),
                    share_recommendation: decision.recommendation,
                    notes: this.subNotes || null
                };

                try {
                    let res = await fetch('__SUPABASE_URL__/rest/v1/weekly_submissions', {
                        method: 'POST',
                        headers: {
                            'apikey': '__SUPABASE_KEY__',
                            'Authorization': 'Bearer __SUPABASE_KEY__',
                            'Content-Type': 'application/json',
                            'Prefer': 'return=representation'
                        },
                        body: JSON.stringify(payload)
                    });

                    if (!res.ok) {
                        const errData = await res.json().catch(() => ({}));
                        const errStr = JSON.stringify(errData);
                        if (errStr.includes('share_recommendation')) {
                            const fallbackPayload = { ...payload };
                            delete fallbackPayload.share_recommendation;
                            res = await fetch('__SUPABASE_URL__/rest/v1/weekly_submissions', {
                                method: 'POST',
                                headers: {
                                    'apikey': '__SUPABASE_KEY__',
                                    'Authorization': 'Bearer __SUPABASE_KEY__',
                                    'Content-Type': 'application/json',
                                    'Prefer': 'return=representation'
                                },
                                body: JSON.stringify(fallbackPayload)
                            });
                        }
                    }

                    if (!res.ok) {
                        const errData = await res.json().catch(() => ({}));
                        throw new Error(errData.message || 'Failed to submit weekly submission');
                    }

                    // Upsert into band_registry
                    const resBr = await fetch('__SUPABASE_URL__/rest/v1/band_registry?on_conflict=band_name', {
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

                    if (!resBr.ok) {
                        const errData = await resBr.json().catch(() => ({}));
                        throw new Error(errData.message || 'Failed to update band registry');
                    }

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

                const lines = this.bulkCsv.trim().split(/\\r?\\n/);
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

                    const decision = this.getBandShareDecision(band_name);

                    items.push({
                        week,
                        band_name,
                        direct_views,
                        indirect_views,
                        total_saves,
                        interaction_type,
                        interaction_score: this.calcScore(interaction_type),
                        shared: interaction_type.includes('shared'),
                        share_recommendation: decision.recommendation,
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

                // Deduplicate bandRegistryItems by band_name keeping the last entry
                const uniqueRegistryMap = new Map();
                for (let item of bandRegistryItems) {
                    uniqueRegistryMap.set(item.band_name.toLowerCase(), item);
                }
                const deduplicatedRegistryItems = Array.from(uniqueRegistryMap.values());

                try {
                    let res = await fetch('__SUPABASE_URL__/rest/v1/weekly_submissions', {
                        method: 'POST',
                        headers: {
                            'apikey': '__SUPABASE_KEY__',
                            'Authorization': 'Bearer __SUPABASE_KEY__',
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(items)
                    });

                    if (!res.ok) {
                        const errData = await res.json().catch(() => ({}));
                        const errStr = JSON.stringify(errData);
                        if (errStr.includes('share_recommendation')) {
                            const fallbackItems = items.map(item => {
                                const copy = { ...item };
                                delete copy.share_recommendation;
                                return copy;
                            });
                            res = await fetch('__SUPABASE_URL__/rest/v1/weekly_submissions', {
                                method: 'POST',
                                headers: {
                                    'apikey': '__SUPABASE_KEY__',
                                    'Authorization': 'Bearer __SUPABASE_KEY__',
                                    'Content-Type': 'application/json'
                                },
                                body: JSON.stringify(fallbackItems)
                            });
                        }
                    }

                    if (!res.ok) {
                        const errData = await res.json().catch(() => ({}));
                        throw new Error(errData.message || 'Bulk insert failed');
                    }

                    // Upsert into band_registry
                    const resBr = await fetch('__SUPABASE_URL__/rest/v1/band_registry?on_conflict=band_name', {
                        method: 'POST',
                        headers: {
                            'apikey': '__SUPABASE_KEY__',
                            'Authorization': 'Bearer __SUPABASE_KEY__',
                            'Content-Type': 'application/json',
                            'Prefer': 'resolution=merge-duplicates'
                        },
                        body: JSON.stringify(deduplicatedRegistryItems)
                    });

                    if (!resBr.ok) {
                        const errData = await resBr.json().catch(() => ({}));
                        throw new Error(errData.message || 'Bulk registry update failed');
                    }

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
                                class="flex-1 px-2 md:px-3 py-3 bebas text-base md:text-xl transition-all">🎸 TOURS</button>
                        <button @click="viewMode = 'playlist'" :class="viewMode === 'playlist' ? 'bg-[#FFCC00] text-black' : 'text-white'"
                                class="flex-1 px-2 md:px-3 py-3 bebas text-base md:text-xl transition-all border-l-2 md:border-l-4 border-white">PUNK IN PROGRESS</button>
                        <button @click="viewMode = 'admin'" :class="viewMode === 'admin' ? 'bg-[#FF5733] text-black' : 'text-white'"
                                class="flex-1 px-2 md:px-3 py-3 bebas text-base md:text-xl transition-all border-l-2 md:border-l-4 border-white">ADMIN</button>
                        <button @click="viewMode = 'scan'" :class="viewMode === 'scan' ? 'bg-white text-black' : 'text-white'"
                                class="flex-1 px-2 md:px-3 py-3 bebas text-base md:text-xl transition-all border-l-2 md:border-l-4 border-white">SCAN</button>
                    </div>

                    <button @click="forceRefresh()"
                            class="w-full md:w-2/12 bg-acid-lime text-black border-4 border-black p-4 text-xl md:text-2xl bebas hover:translate-x-[-2px] hover:translate-y-[-2px] shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] transition-all">
                        REFRESH
                    </button>
                </div>

                <!-- City Pills -->
                <div class="flex flex-wrap gap-2" x-show="viewMode === 'tours'">
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

            <!-- EXPANDABLE BAND DETAIL MODAL -->
            <div x-show="bandModalOpen" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 overflow-y-auto" x-cloak>
                <div class="brutal-card bg-white text-black p-6 w-full max-w-3xl relative my-8" @click.away="bandModalOpen = false">
                    <button @click="bandModalOpen = false" class="absolute top-4 right-4 font-bold text-3xl hover:text-[#FF5733] transition-colors">&times;</button>

                    <template x-if="selectedBandAnalytics">
                        <div>
                            <div class="border-b-4 border-black pb-4 mb-6">
                                <span class="bg-[#CCFF00] text-black font-bold px-2 py-0.5 mono text-xs uppercase border border-black" x-text="selectedBandAnalytics.growth_trajectory"></span>
                                <h2 class="bebas text-5xl mt-2 leading-none" x-text="selectedBandAnalytics.band_name"></h2>
                                <p class="mono text-xs text-gray-600 mt-1 uppercase" x-text="'Tracked for ' + selectedBandAnalytics.days_tracked + ' days &middot; Peak: ' + (selectedBandAnalytics.peak_listener_count || 0).toLocaleString() + ' listeners (' + (selectedBandAnalytics.peak_date || 'N/A') + ')'"></p>
                            </div>

                            <!-- Growth Metrics Grid -->
                            <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
                                <div class="p-3 bg-gray-100 border-2 border-black mono">
                                    <p class="text-[10px] text-gray-500 uppercase font-bold">LISTENERS</p>
                                    <p class="bebas text-2xl mt-1" x-text="(selectedBandAnalytics.latest_listener_count || 0).toLocaleString()"></p>
                                </div>
                                <div class="p-3 bg-gray-100 border-2 border-black mono">
                                    <p class="text-[10px] text-gray-500 uppercase font-bold">WoW GROWTH</p>
                                    <p class="bebas text-2xl mt-1" :class="(selectedBandAnalytics.week_over_week_growth_pct || 0) >= 0 ? 'text-green-600' : 'text-red-600'" x-text="((selectedBandAnalytics.week_over_week_growth_pct || 0) >= 0 ? '+' : '') + selectedBandAnalytics.week_over_week_growth_pct + '%'"></p>
                                </div>
                                <div class="p-3 bg-gray-100 border-2 border-black mono">
                                    <p class="text-[10px] text-gray-500 uppercase font-bold">MoM GROWTH</p>
                                    <p class="bebas text-2xl mt-1" :class="(selectedBandAnalytics.month_over_month_growth_pct || 0) >= 0 ? 'text-green-600' : 'text-red-600'" x-text="((selectedBandAnalytics.month_over_month_growth_pct || 0) >= 0 ? '+' : '') + selectedBandAnalytics.month_over_month_growth_pct + '%'"></p>
                                </div>
                                <div class="p-3 bg-black text-[#CCFF00] border-2 border-black mono">
                                    <p class="text-[10px] text-yellow-300 uppercase font-bold">MOMENTUM</p>
                                    <p class="bebas text-2xl mt-1" x-text="selectedBandAnalytics.momentum_score + ' / 100'"></p>
                                </div>
                            </div>

                            <!-- Featured History, Share Stats & Tour Activity -->
                            <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
                                <div class="border-2 border-black p-4 bg-yellow-50">
                                    <h4 class="bebas text-2xl mb-2 border-b-2 border-black pb-1 uppercase">FEATURED HISTORY</h4>
                                    <p class="mono text-xs mb-1">Total Times Featured: <strong x-text="selectedBandAnalytics.total_features || 0"></strong></p>
                                    <p class="mono text-xs mb-1">First Featured: <strong x-text="selectedBandAnalytics.first_featured_week || 'None'"></strong></p>
                                    <p class="mono text-xs">Last Featured: <strong x-text="selectedBandAnalytics.last_featured_week || 'None'"></strong></p>
                                </div>

                                <div class="border-2 border-black p-4 bg-orange-50">
                                    <h4 class="bebas text-2xl mb-2 border-b-2 border-black pb-1 uppercase">SHARE TRACKING & LIFT</h4>
                                    <p class="mono text-xs mb-1">Total Shares: <strong x-text="selectedBandAnalytics.total_shares || 0"></strong></p>
                                    <p class="mono text-xs mb-1">Last Shared: <strong x-text="selectedBandAnalytics.last_shared_week || 'Never'"></strong></p>
                                    <p class="mono text-xs mb-1">Avg Lift After Share: <strong :class="(selectedBandAnalytics.avg_growth_after_share_pct || 0) >= 0 ? 'text-green-700' : 'text-red-600'" x-text="((selectedBandAnalytics.avg_growth_after_share_pct || 0) >= 0 ? '+' : '') + (selectedBandAnalytics.avg_growth_after_share_pct || 0) + '%'"></strong></p>
                                    <p class="mono text-xs">Latest Share Lift: <strong x-text="(selectedBandAnalytics.share_lift_pct || 0) + '% (' + (selectedBandAnalytics.share_lift_absolute >= 0 ? '+' : '') + (selectedBandAnalytics.share_lift_absolute || 0) + ' listeners)'"></strong></p>
                                </div>

                                <div class="border-2 border-black p-4 bg-purple-50">
                                    <h4 class="bebas text-2xl mb-2 border-b-2 border-black pb-1 uppercase">TOUR ACTIVITY</h4>
                                    <template x-if="getBandTours(selectedBandAnalytics.band_name).length > 0">
                                        <div class="space-y-2 max-h-32 overflow-y-auto">
                                            <template x-for="t in getBandTours(selectedBandAnalytics.band_name)" :key="t.id">
                                                <p class="mono text-xs">
                                                    🎸 <span class="font-bold" x-text="t.city"></span> @ <span x-text="t.venue"></span> (<span x-text="t.event_date"></span>)
                                                </p>
                                            </template>
                                        </div>
                                    </template>
                                    <template x-if="getBandTours(selectedBandAnalytics.band_name).length === 0">
                                        <p class="mono text-xs text-gray-500 uppercase">NO UPCOMING TOURS RECORDED.</p>
                                    </template>
                                </div>
                            </div>

                            <!-- Recent Historical Snapshots Log -->
                            <div class="border-2 border-black p-4 bg-white">
                                <h4 class="bebas text-2xl mb-2 border-b-2 border-black pb-1 uppercase">SNAPSHOT LOG HISTORY</h4>
                                <div class="max-h-40 overflow-y-auto">
                                    <table class="w-full text-left mono text-xs border-collapse">
                                        <thead>
                                            <tr class="bg-gray-100">
                                                <th class="p-1 border border-black">DATE</th>
                                                <th class="p-1 border border-black">WEEK</th>
                                                <th class="p-1 border border-black text-right">LISTENERS</th>
                                                <th class="p-1 border border-black text-right">FOLLOWERS</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            <template x-for="s in getBandSnapshots(selectedBandAnalytics.band_name)" :key="s.id">
                                                <tr>
                                                    <td class="p-1 border border-black" x-text="s.recorded_date"></td>
                                                    <td class="p-1 border border-black" x-text="s.snapshot_week || 'N/A'"></td>
                                                    <td class="p-1 border border-black text-right font-bold" x-text="(s.listener_count || 0).toLocaleString()"></td>
                                                    <td class="p-1 border border-black text-right" x-text="(s.follower_count || 0).toLocaleString()"></td>
                                                </tr>
                                            </template>
                                        </tbody>
                                    </table>
                                </div>
                            </div>

                            <!-- External Links -->
                            <div class="mt-6 flex gap-3">
                                <template x-if="selectedBandAnalytics.spotify_id">
                                    <a :href="'https://open.spotify.com/artist/' + selectedBandAnalytics.spotify_id" target="_blank"
                                       class="flex-1 bg-[#1DB954] text-white text-center p-3 bebas text-xl border-2 border-black hover:bg-black hover:text-white transition-colors uppercase">
                                        Open on Spotify
                                    </a>
                                </template>
                            </div>
                        </div>
                    </template>
                </div>
            </div>

            <!-- Admin Panel Section (Phase 1) -->
            <div x-show="viewMode === 'admin'" x-cloak>
                <div class="mb-8 bg-[#FF5733] border-4 border-black p-6 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] text-black">
                    <h2 class="bebas text-4xl mb-2 tracking-wide uppercase">⚡ DIY PUNK ADMIN DASHBOARD ⚡</h2>
                    <p class="mono text-sm leading-relaxed">
                        Submit weekly band performance data & monitor real-time portfolio band growth analytics & share recommendations.
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

                <!-- Authenticated Admin Section -->
                <div x-show="adminAuth" class="space-y-8">
                    <!-- Sub-Navigation for Admin: Submissions vs Analytics -->
                    <div class="flex border-4 border-white overflow-hidden bg-black mb-8">
                        <button @click="adminTab = 'submissions'" :class="adminTab === 'submissions' ? 'bg-[#CCFF00] text-black font-bold' : 'bg-black text-white'" class="flex-1 py-3 bebas text-2xl transition-all">⚡ SUBMISSIONS & LOGS</button>
                        <button @click="adminTab = 'analytics'" :class="adminTab === 'analytics' ? 'bg-[#00E5FF] text-black font-bold' : 'bg-black text-white'" class="flex-1 py-3 bebas text-2xl transition-all border-l-4 border-white">📊 BAND ANALYTICS & SHARE DASHBOARD</button>
                    </div>

                    <!-- SUBMISSIONS TAB -->
                    <div x-show="adminTab === 'submissions'" class="space-y-8">
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

                        <!-- Share Decision Framework Live Card Preview -->
                        <div class="mt-4 p-4 border-4 border-black bg-yellow-50 space-y-3 mono text-xs shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
                            <div class="flex justify-between items-center border-b-2 border-black pb-2">
                                <span class="font-bold text-sm uppercase">🎯 SHARE DECISION RECOMMENDATION:</span>
                                <span :class="getBandShareDecision(subBandName).badge_bg + ' ' + getBandShareDecision(subBandName).badge_text"
                                      class="px-3 py-1 font-bold border border-black text-xs uppercase"
                                      x-text="getBandShareDecision(subBandName).recommendation"></span>
                            </div>

                            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
                                <div class="p-2 bg-white border border-black">
                                    <p class="text-[10px] text-gray-500 uppercase font-bold">Featured</p>
                                    <p class="bebas text-xl" x-text="getBandShareDecision(subBandName).appearances + 'x'"></p>
                                </div>
                                <div class="p-2 bg-white border border-black">
                                    <p class="text-[10px] text-gray-500 uppercase font-bold">Shared</p>
                                    <p class="bebas text-xl" x-text="getBandShareDecision(subBandName).times_shared + 'x'"></p>
                                </div>
                                <div class="p-2 bg-white border border-black">
                                    <p class="text-[10px] text-gray-500 uppercase font-bold">Success Rate</p>
                                    <p class="bebas text-xl text-green-700" x-text="getBandShareDecision(subBandName).success_rate_pct + '%'"></p>
                                </div>
                                <div class="p-2 bg-white border border-black">
                                    <p class="text-[10px] text-gray-500 uppercase font-bold">Avg Score</p>
                                    <p class="bebas text-xl text-purple-700" x-text="getBandShareDecision(subBandName).avg_interaction"></p>
                                </div>
                            </div>

                            <div class="flex justify-between items-center text-xs pt-1 border-t border-black">
                                <span>Score: <strong x-text="currentScore"></strong> &middot; Derived Shared: <strong x-text="currentShared ? 'YES' : 'NO'"></strong></span>
                                <span class="font-bold text-gray-700" x-text="'Tier: ' + getBandShareDecision(subBandName).tier"></span>
                            </div>
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

                    <!-- ANALYTICS TAB -->
                    <div x-show="adminTab === 'analytics'" class="space-y-8">
                        <!-- Sub-Navigation for Analytics -->
                        <div class="flex border-4 border-white overflow-hidden">
                            <button @click="analyticsSubTab = 'overview'" :class="analyticsSubTab === 'overview' ? 'bg-[#00E5FF] text-black font-bold' : 'bg-black text-white'" class="flex-1 py-3 bebas text-2xl transition-all">📊 OVERVIEW & BAND TRACKER</button>
                            <button @click="analyticsSubTab = 'shares'" :class="analyticsSubTab === 'shares' ? 'bg-[#FF5733] text-black font-bold' : 'bg-black text-white'" class="flex-1 py-3 bebas text-2xl transition-all border-l-4 border-white">🚀 SHARE IMPACT DASHBOARD</button>
                        </div>

                        <!-- OVERVIEW SUB-TAB -->
                        <div x-show="analyticsSubTab === 'overview'" class="space-y-8">
                            <!-- Section 1: Overview Cards -->
                            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                                <div class="brutal-card p-6 bg-white text-black border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                                    <p class="mono text-xs text-gray-500 uppercase font-bold">Bands Tracked</p>
                                    <h3 class="bebas text-5xl mt-2" x-text="totalBandsTracked"></h3>
                                    <p class="mono text-[10px] text-gray-400 mt-1 uppercase">Historical Snapshot Audit</p>
                                </div>
                                <div class="brutal-card p-6 bg-white text-black border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                                    <p class="mono text-xs text-gray-500 uppercase font-bold">Total Listeners</p>
                                    <h3 class="bebas text-5xl mt-2 acid-lime" x-text="totalListeners"></h3>
                                    <p class="mono text-[10px] text-gray-400 mt-1 uppercase">Combined Spotify Audience</p>
                                </div>
                                <div class="brutal-card p-6 bg-white text-black border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                                    <p class="mono text-xs text-gray-500 uppercase font-bold">Avg WoW Growth</p>
                                    <h3 class="bebas text-5xl mt-2" :class="avgWowGrowth.startsWith('+') ? 'text-green-600' : 'text-red-600'" x-text="avgWowGrowth"></h3>
                                    <p class="mono text-[10px] text-gray-400 mt-1 uppercase">Weekly Portfolio Velocity</p>
                                </div>
                                <div class="brutal-card p-6 bg-white text-black border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                                    <p class="mono text-xs text-gray-500 uppercase font-bold">Most Explosive Act</p>
                                    <h3 class="bebas text-4xl mt-2 truncate text-purple-700" x-text="mostExplosiveBand"></h3>
                                    <p class="mono text-[10px] text-gray-400 mt-1 uppercase">Top Weekly Velocity</p>
                                </div>
                            </div>

                            <!-- Filters & Controls -->
                            <div class="flex flex-wrap items-center justify-between gap-4 bg-gray-900 p-4 border-4 border-white">
                                <div class="flex flex-wrap gap-2">
                                    <button @click="analyticsFilter = 'all'" :class="analyticsFilter === 'all' ? 'bg-[#CCFF00] text-black font-bold' : 'text-white'" class="px-3 py-1.5 mono text-xs uppercase border border-white">ALL BANDS</button>
                                    <button @click="analyticsFilter = 'explosive'" :class="analyticsFilter === 'explosive' ? 'bg-[#CCFF00] text-black font-bold' : 'text-white'" class="px-3 py-1.5 mono text-xs uppercase border border-white">🚀 EXPLOSIVE ONLY</button>
                                    <button @click="analyticsFilter = 'featured'" :class="analyticsFilter === 'featured' ? 'bg-[#CCFF00] text-black font-bold' : 'text-white'" class="px-3 py-1.5 mono text-xs uppercase border border-white">🔥 FEATURED BY US</button>
                                    <button @click="analyticsFilter = 'shared'" :class="analyticsFilter === 'shared' ? 'bg-[#CCFF00] text-black font-bold' : 'text-white'" class="px-3 py-1.5 mono text-xs uppercase border border-white">📲 SHARED BANDS</button>
                                    <button @click="analyticsFilter = 'under100k'" :class="analyticsFilter === 'under100k' ? 'bg-[#CCFF00] text-black font-bold' : 'text-white'" class="px-3 py-1.5 mono text-xs uppercase border border-white">💎 HIDDEN GEMS (&lt;100K)</button>
                                </div>

                                <div class="flex items-center gap-2 mono text-xs">
                                    <span class="text-gray-400 uppercase font-bold">SORT BY:</span>
                                    <select x-model="analyticsSortKey" class="bg-black text-white p-2 border border-white focus:outline-none uppercase font-bold">
                                        <option value="momentum_score">Momentum Score</option>
                                        <option value="latest_listener_count">Listeners</option>
                                        <option value="week_over_week_growth_pct">WoW Growth %</option>
                                        <option value="month_over_month_growth_pct">MoM Growth %</option>
                                        <option value="band_name">Band Name</option>
                                    </select>
                                    <button @click="analyticsSortAsc = !analyticsSortAsc" class="bg-white text-black p-2 font-bold border border-white" x-text="analyticsSortAsc ? '▲' : '▼'"></button>
                                </div>
                            </div>

                            <!-- Section 2: Sortable Band Table -->
                            <div class="brutal-card p-6 bg-white text-black overflow-x-auto">
                                <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase flex justify-between items-center">
                                    <span>BAND GROWTH TRACKER</span>
                                    <span class="mono text-xs font-normal text-gray-500" x-text="'Showing ' + filteredAnalytics.length + ' bands'"></span>
                                </h3>

                                <table class="w-full text-left border-collapse mono text-xs">
                                    <thead>
                                        <tr class="bg-black text-white uppercase">
                                            <th class="p-3 border border-black cursor-pointer hover:bg-gray-800" @click="analyticsSortKey = 'band_name'; analyticsSortAsc = !analyticsSortAsc">BAND NAME</th>
                                            <th class="p-3 border border-black cursor-pointer hover:bg-gray-800 text-right" @click="analyticsSortKey = 'latest_listener_count'; analyticsSortAsc = !analyticsSortAsc">LISTENERS</th>
                                            <th class="p-3 border border-black cursor-pointer hover:bg-gray-800 text-right" @click="analyticsSortKey = 'week_over_week_growth_pct'; analyticsSortAsc = !analyticsSortAsc">WoW %</th>
                                            <th class="p-3 border border-black cursor-pointer hover:bg-gray-800 text-right" @click="analyticsSortKey = 'month_over_month_growth_pct'; analyticsSortAsc = !analyticsSortAsc">MoM %</th>
                                            <th class="p-3 border border-black cursor-pointer hover:bg-gray-800 text-center" @click="analyticsSortKey = 'momentum_score'; analyticsSortAsc = !analyticsSortAsc">MOMENTUM</th>
                                            <th class="p-3 border border-black cursor-pointer hover:bg-gray-800 text-center" @click="analyticsSortKey = 'growth_trajectory'; analyticsSortAsc = !analyticsSortAsc">TRAJECTORY</th>
                                            <th class="p-3 border border-black text-center">ACTION</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <template x-for="band in filteredAnalytics" :key="band.id || band.band_name">
                                            <tr class="hover:bg-yellow-100 transition-colors cursor-pointer" @click="openBandModal(band)">
                                                <td class="p-3 border border-black font-bold text-sm">
                                                    <div class="flex items-center gap-2">
                                                        <span x-text="band.band_name"></span>
                                                        <template x-if="band.total_features > 0">
                                                            <span class="bg-black text-yellow-300 text-[9px] px-1.5 py-0.5 font-bold uppercase">FEATURED</span>
                                                        </template>
                                                    </div>
                                                </td>
                                                <td class="p-3 border border-black text-right font-bold text-sm" x-text="(band.latest_listener_count || 0).toLocaleString()"></td>
                                                <td class="p-3 border border-black text-right font-bold" :class="(band.week_over_week_growth_pct || 0) >= 0 ? 'text-green-700' : 'text-red-600'" x-text="((band.week_over_week_growth_pct || 0) >= 0 ? '+' : '') + (band.week_over_week_growth_pct || 0) + '%'"></td>
                                                <td class="p-3 border border-black text-right font-bold" :class="(band.month_over_month_growth_pct || 0) >= 0 ? 'text-green-700' : 'text-red-600'" x-text="((band.month_over_month_growth_pct || 0) >= 0 ? '+' : '') + (band.month_over_month_growth_pct || 0) + '%'"></td>
                                                <td class="p-3 border border-black text-center">
                                                    <div class="inline-block bg-black text-[#CCFF00] font-bold px-2.5 py-1 text-xs border border-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                                                        🔥 <span x-text="band.momentum_score || 0"></span>/100
                                                    </div>
                                                </td>
                                                <td class="p-3 border border-black text-center font-bold">
                                                    <span x-show="band.growth_trajectory === 'explosive'" class="bg-purple-200 text-purple-900 border border-purple-800 px-2 py-0.5 text-[10px] uppercase">📈 EXPLOSIVE</span>
                                                    <span x-show="band.growth_trajectory === 'steady'" class="bg-green-200 text-green-900 border border-green-800 px-2 py-0.5 text-[10px] uppercase">📊 STEADY</span>
                                                    <span x-show="band.growth_trajectory === 'flat'" class="bg-yellow-200 text-yellow-900 border border-yellow-800 px-2 py-0.5 text-[10px] uppercase">▬ FLAT</span>
                                                    <span x-show="band.growth_trajectory === 'declining'" class="bg-red-200 text-red-900 border border-red-800 px-2 py-0.5 text-[10px] uppercase">📉 DECLINING</span>
                                                </td>
                                                <td class="p-3 border border-black text-center" @click.stop>
                                                    <button @click="openBandModal(band)" class="bg-black text-white hover:bg-[#CCFF00] hover:text-black px-3 py-1 font-bold text-xs uppercase border border-black transition-colors">
                                                        INSPECT
                                                    </button>
                                                </td>
                                            </tr>
                                        </template>

                                        <template x-if="filteredAnalytics.length === 0">
                                            <tr>
                                                <td colspan="7" class="p-8 text-center text-gray-500 font-bold uppercase">NO ANALYTICS SNAPSHOTS RECORDED YET. RUN SUNDAY SNAPSHOT JOB.</td>
                                            </tr>
                                        </template>
                                    </tbody>
                                </table>
                            </div>

                            <!-- Section 3: Visual Trends -->
                            <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
                                <!-- Bar Chart: Top Momentum Bands -->
                                <div class="lg:col-span-7 brutal-card p-6 bg-white text-black">
                                    <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase">🔥 TOP MOMENTUM BANDS THIS MONTH</h3>
                                    <div class="space-y-3">
                                        <template x-for="band in [...analyticsSummary].sort((a,b) => (b.momentum_score||0) - (a.momentum_score||0)).slice(0, 5)" :key="band.band_name">
                                            <div>
                                                <div class="flex justify-between text-xs mono font-bold uppercase mb-1">
                                                    <span x-text="band.band_name"></span>
                                                    <span x-text="(band.momentum_score || 0) + ' / 100'"></span>
                                                </div>
                                                <div class="w-full bg-gray-200 h-6 border-2 border-black relative overflow-hidden">
                                                    <div class="bg-[#CCFF00] h-full transition-all duration-500 border-r-2 border-black" :style="'width: ' + Math.max(5, band.momentum_score || 0) + '%'"></div>
                                                </div>
                                            </div>
                                        </template>
                                        <template x-if="analyticsSummary.length === 0">
                                            <p class="mono text-xs text-gray-500 uppercase">NO MOMENTUM DATA AVAILABLE YET.</p>
                                        </template>
                                    </div>
                                </div>

                                <!-- Trajectory Breakdown Card -->
                                <div class="lg:col-span-5 brutal-card p-6 bg-white text-black flex flex-col justify-between">
                                    <div>
                                        <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase">📈 TRAJECTORY DISTRIBUTION</h3>
                                        <div class="space-y-4 mono text-xs">
                                            <div class="flex justify-between items-center p-3 bg-purple-100 border-2 border-black">
                                                <span class="font-bold text-purple-900 uppercase">📈 EXPLOSIVE</span>
                                                <span class="bebas text-2xl" x-text="analyticsSummary.filter(b => b.growth_trajectory === 'explosive').length"></span>
                                            </div>
                                            <div class="flex justify-between items-center p-3 bg-green-100 border-2 border-black">
                                                <span class="font-bold text-green-900 uppercase">📊 STEADY</span>
                                                <span class="bebas text-2xl" x-text="analyticsSummary.filter(b => b.growth_trajectory === 'steady').length"></span>
                                            </div>
                                            <div class="flex justify-between items-center p-3 bg-yellow-100 border-2 border-black">
                                                <span class="font-bold text-yellow-900 uppercase">▬ FLAT</span>
                                                <span class="bebas text-2xl" x-text="analyticsSummary.filter(b => b.growth_trajectory === 'flat').length"></span>
                                            </div>
                                            <div class="flex justify-between items-center p-3 bg-red-100 border-2 border-black">
                                                <span class="font-bold text-red-900 uppercase">📉 DECLINING</span>
                                                <span class="bebas text-2xl" x-text="analyticsSummary.filter(b => b.growth_trajectory === 'declining').length"></span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- SHARE IMPACT DASHBOARD SUB-TAB -->
                        <div x-show="analyticsSubTab === 'shares'" class="space-y-8">
                            <!-- Share Impact Summary Stats -->
                            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                                <div class="brutal-card p-6 bg-white text-black border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                                    <p class="mono text-xs text-gray-500 uppercase font-bold">Total Bands Shared</p>
                                    <h3 class="bebas text-5xl mt-2 text-orange-600" x-text="totalBandsShared"></h3>
                                    <p class="mono text-[10px] text-gray-400 mt-1 uppercase">Promoted on Socials</p>
                                </div>
                                <div class="brutal-card p-6 bg-white text-black border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                                    <p class="mono text-xs text-gray-500 uppercase font-bold">Avg Shares per Band</p>
                                    <h3 class="bebas text-5xl mt-2" x-text="avgSharesPerBand"></h3>
                                    <p class="mono text-[10px] text-gray-400 mt-1 uppercase">Frequency Ratio</p>
                                </div>
                                <div class="brutal-card p-6 bg-white text-black border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                                    <p class="mono text-xs text-gray-500 uppercase font-bold">Most Shared Band</p>
                                    <h3 class="bebas text-4xl mt-2 truncate text-purple-700" x-text="mostSharedBand"></h3>
                                    <p class="mono text-[10px] text-gray-400 mt-1 uppercase">Top Shared Artist</p>
                                </div>
                                <div class="brutal-card p-6 bg-white text-black border-4 border-black shadow-[6px_6px_0px_0px_rgba(0,0,0,1)]">
                                    <p class="mono text-xs text-gray-500 uppercase font-bold">High ROI Shares (&gt;10% Lift)</p>
                                    <h3 class="bebas text-5xl mt-2 acid-lime" x-text="highImpactSharesCount"></h3>
                                    <p class="mono text-[10px] text-gray-400 mt-1 uppercase">High Impact Conversions</p>
                                </div>
                            </div>

                            <!-- Shared Bands Report Table -->
                            <div class="brutal-card p-6 bg-white text-black overflow-x-auto">
                                <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase flex justify-between items-center">
                                    <span>📲 SHARED BANDS PERFORMANCE REPORT</span>
                                    <span class="mono text-xs font-normal text-gray-500" x-text="sharedBandsList.length + ' Bands Shared'"></span>
                                </h3>

                                <table class="w-full text-left border-collapse mono text-xs">
                                    <thead>
                                        <tr class="bg-black text-white uppercase">
                                            <th class="p-3 border border-black">BAND NAME</th>
                                            <th class="p-3 border border-black text-center">TIMES SHARED</th>
                                            <th class="p-3 border border-black text-center">LAST SHARED WEEK</th>
                                            <th class="p-3 border border-black text-center">MOMENTUM</th>
                                            <th class="p-3 border border-black text-right">AVG LIFT AFTER SHARE %</th>
                                            <th class="p-3 border border-black text-right">LATEST SHARE LIFT</th>
                                            <th class="p-3 border border-black text-center">ACTION</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <template x-for="band in sharedBandsList" :key="band.id || band.band_name">
                                            <tr class="hover:bg-orange-50 transition-colors cursor-pointer" @click="openBandModal(band)">
                                                <td class="p-3 border border-black font-bold text-sm" x-text="band.band_name"></td>
                                                <td class="p-3 border border-black text-center font-bold" x-text="band.total_shares"></td>
                                                <td class="p-3 border border-black text-center" x-text="band.last_shared_week || 'N/A'"></td>
                                                <td class="p-3 border border-black text-center">
                                                    <div class="inline-block bg-black text-[#CCFF00] font-bold px-2 py-0.5 text-xs">
                                                        🔥 <span x-text="band.momentum_score || 0"></span>
                                                    </div>
                                                </td>
                                                <td class="p-3 border border-black text-right font-bold" :class="(band.avg_growth_after_share_pct || 0) >= 0 ? 'text-green-700' : 'text-red-600'" x-text="((band.avg_growth_after_share_pct || 0) >= 0 ? '+' : '') + (band.avg_growth_after_share_pct || 0) + '%'"></td>
                                                <td class="p-3 border border-black text-right font-bold" :class="(band.share_lift_pct || 0) >= 0 ? 'text-green-700' : 'text-red-600'" x-text="((band.share_lift_pct || 0) >= 0 ? '+' : '') + (band.share_lift_pct || 0) + '% (' + (band.share_lift_absolute >= 0 ? '+' : '') + (band.share_lift_absolute || 0) + ')'"></td>
                                                <td class="p-3 border border-black text-center" @click.stop>
                                                    <button @click="openBandModal(band)" class="bg-black text-white hover:bg-[#CCFF00] hover:text-black px-3 py-1 font-bold text-xs uppercase border border-black">
                                                        INSPECT
                                                    </button>
                                                </td>
                                            </tr>
                                        </template>
                                        <template x-if="sharedBandsList.length === 0">
                                            <tr>
                                                <td colspan="7" class="p-8 text-center text-gray-500 font-bold uppercase">NO BANDS SHARED YET. RECORD SHARED INTERACTIONS IN ADMIN TAB.</td>
                                            </tr>
                                        </template>
                                    </tbody>
                                </table>
                            </div>

                            <!-- Master Share Decision Matrix Widget (The 5 Tiers) -->
                            <div class="brutal-card p-6 bg-white text-black">
                                <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase flex justify-between items-center">
                                    <span>🎯 MASTER SHARE DECISION MATRIX (5 TIERS)</span>
                                    <span class="mono text-xs text-gray-500 font-normal">Analytics & Historical Curation Rules</span>
                                </h3>

                                <div class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
                                    <div class="p-3 bg-green-100 border-2 border-black mono">
                                        <p class="text-[10px] text-green-900 font-bold uppercase">TIER 1: DEFINITE YES 🚀</p>
                                        <p class="bebas text-xl mt-1 text-green-950">Proven engagement</p>
                                        <p class="text-[9px] text-gray-600 mt-1">Shared before & Avg Score &ge; 1.8</p>
                                    </div>
                                    <div class="p-3 bg-lime-100 border-2 border-black mono">
                                        <p class="text-[10px] text-lime-900 font-bold uppercase">TIER 2: LIKELY YES ✅</p>
                                        <p class="bebas text-xl mt-1 text-lime-950">Track record</p>
                                        <p class="text-[9px] text-gray-600 mt-1">Shared before & Saves &gt; 80</p>
                                    </div>
                                    <div class="p-3 bg-amber-100 border-2 border-black mono">
                                        <p class="text-[10px] text-amber-900 font-bold uppercase">TIER 3: MAYBE ⚡</p>
                                        <p class="bebas text-xl mt-1 text-amber-950">Unproven but hot</p>
                                        <p class="text-[9px] text-gray-600 mt-1">Never shared & Saves &gt; 100</p>
                                    </div>
                                    <div class="p-3 bg-blue-100 border-2 border-black mono">
                                        <p class="text-[10px] text-blue-900 font-bold uppercase">TIER 4: LET IT COOK 💭</p>
                                        <p class="bebas text-xl mt-1 text-blue-950">Building momentum</p>
                                        <p class="text-[9px] text-gray-600 mt-1">Never shared & Featured &ge; 2x</p>
                                    </div>
                                    <div class="p-3 bg-red-100 border-2 border-black mono">
                                        <p class="text-[10px] text-red-900 font-bold uppercase">TIER 5: SKIP ❌</p>
                                        <p class="bebas text-xl mt-1 text-red-950">Low track record</p>
                                        <p class="text-[9px] text-gray-600 mt-1">Featured multiple times, low response</p>
                                    </div>
                                </div>

                                <!-- Band Evaluator Quick Search in Matrix -->
                                <div x-data="{ evalBand: 'Ellen May' }" class="p-4 bg-gray-900 text-white border-2 border-black">
                                    <label class="block mono text-xs font-bold uppercase mb-2 text-[#CCFF00]">EVALUATE ANY BAND AGAINST FRAMEWORK:</label>
                                    <div class="flex flex-col sm:flex-row gap-3">
                                        <input type="text" x-model="evalBand" placeholder="Type band name (e.g. RATSALAD)..." class="flex-1 bg-black text-white p-2 border border-white mono text-sm">
                                    </div>

                                    <div class="mt-4 p-3 bg-white text-black border-2 border-black flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                                        <div>
                                            <h4 class="bebas text-3xl leading-none" x-text="evalBand || 'Band Name'"></h4>
                                            <p class="mono text-xs text-gray-600 mt-1 uppercase">
                                                Featured: <strong x-text="getBandShareDecision(evalBand).appearances + 'x'"></strong> &middot;
                                                Shared: <strong x-text="getBandShareDecision(evalBand).times_shared + 'x'"></strong> &middot;
                                                Success Rate: <strong x-text="getBandShareDecision(evalBand).success_rate_pct + '%'"></strong> &middot;
                                                Saves: <strong x-text="getBandShareDecision(evalBand).total_saves"></strong> &middot;
                                                Avg Score: <strong x-text="getBandShareDecision(evalBand).avg_interaction"></strong>
                                            </p>
                                        </div>
                                        <span :class="getBandShareDecision(evalBand).badge_bg + ' ' + getBandShareDecision(evalBand).badge_text"
                                              class="px-4 py-2 font-bold border-2 border-black bebas text-xl uppercase tracking-wide"
                                              x-text="getBandShareDecision(evalBand).recommendation"></span>
                                    </div>
                                </div>
                            </div>

                            <!-- Grid: Recommendations & High ROI Shares -->
                            <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                                <!-- Next Share Recommendations Widget -->
                                <div class="brutal-card p-6 bg-white text-black">
                                    <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase flex justify-between items-center">
                                        <span>🚀 NEXT SHARE CANDIDATES</span>
                                        <span class="mono text-xs text-gray-500 font-normal">Momentum &gt; 70 &bull; Not Shared</span>
                                    </h3>
                                    <div class="space-y-3">
                                        <template x-for="candidate in shareCandidates" :key="candidate.id || candidate.band_name">
                                            <div class="p-3 border-2 border-black bg-yellow-50 flex items-center justify-between shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                                                <div>
                                                    <h4 class="bebas text-2xl leading-none" x-text="candidate.band_name"></h4>
                                                    <p class="mono text-[10px] text-gray-600 mt-1 uppercase">
                                                        Listeners: <strong x-text="(candidate.latest_listener_count || 0).toLocaleString()"></strong> &middot;
                                                        WoW: <strong :class="(candidate.week_over_week_growth_pct || 0) >= 0 ? 'text-green-700' : 'text-red-600'" x-text="((candidate.week_over_week_growth_pct || 0) >= 0 ? '+' : '') + candidate.week_over_week_growth_pct + '%'"></strong>
                                                    </p>
                                                </div>
                                                <div class="flex items-center gap-3">
                                                    <span class="bg-black text-[#CCFF00] font-bold px-2 py-1 mono text-xs">🔥 <span x-text="candidate.momentum_score"></span>/100</span>
                                                    <button @click="openBandModal(candidate)" class="bg-[#CCFF00] text-black hover:bg-black hover:text-white px-3 py-1 bebas text-lg border border-black transition-colors uppercase">
                                                        RECOMMEND
                                                    </button>
                                                </div>
                                            </div>
                                        </template>
                                        <template x-if="shareCandidates.length === 0">
                                            <p class="mono text-xs text-gray-500 uppercase font-bold p-4 text-center">NO UNSHARED CANDIDATES WITH MOMENTUM &gt; 70 CURRENTLY.</p>
                                        </template>
                                    </div>
                                </div>

                                <!-- High ROI Shares Report -->
                                <div class="brutal-card p-6 bg-white text-black">
                                    <h3 class="bebas text-3xl mb-4 border-b-4 border-black pb-2 uppercase flex justify-between items-center">
                                        <span>🔥 HIGH ROI SHARES REPORT</span>
                                        <span class="mono text-xs text-gray-500 font-normal">&gt;10% Lift After Share</span>
                                    </h3>
                                    <div class="space-y-3">
                                        <template x-for="roi in highRoiSharesList" :key="roi.id || roi.band_name">
                                            <div class="p-3 border-2 border-black bg-green-50 flex items-center justify-between shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                                                <div>
                                                    <h4 class="bebas text-2xl leading-none" x-text="roi.band_name"></h4>
                                                    <p class="mono text-[10px] text-gray-600 mt-1 uppercase">
                                                        Shared: <strong x-text="roi.total_shares + 'x'"></strong> &middot;
                                                        Last: <strong x-text="roi.last_shared_week"></strong> &middot;
                                                        Listeners: <strong x-text="(roi.latest_listener_count || 0).toLocaleString()"></strong>
                                                    </p>
                                                </div>
                                                <div class="text-right">
                                                    <span class="bebas text-2xl text-green-700 block" x-text="'+' + roi.avg_growth_after_share_pct + '% LIFT'"></span>
                                                </div>
                                            </div>
                                        </template>
                                        <template x-if="highRoiSharesList.length === 0">
                                            <p class="mono text-xs text-gray-500 uppercase font-bold p-4 text-center">NO HIGH-ROI SHARES (&gt;10% LIFT) RECORDED YET.</p>
                                        </template>
                                    </div>
                                </div>
                            </div>
                        </div>
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
