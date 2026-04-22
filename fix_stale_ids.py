import os
import sys
from supabase import create_client

# This script updates stale Songkick IDs in the database.
# 31xxx range IDs were erroneously pointing to Nigeria.
# 28xxx range is correct for Spain.

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL and SUPABASE_KEY must be set in environment.")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

CORRECTIONS = {
    "Granada": "28739",
    "Málaga": "28756",
    "Cádiz": "28733",
    "Córdoba": "28734",
    "Almería": "28727",
    "Sevilla": "28761",
    "Madrid": "28755",
    "Jerez": "28745",
    "Marbella": "28749",
    "Estepona": "28738",
    "Algeciras": "28704",
    "Fuengirola": "28740"
}

print("--- Updating Songkick IDs in 'locations' table ---")
for city, correct_id in CORRECTIONS.items():
    try:
        res = supabase.table("locations").update({"songkick_id": correct_id}).eq("city", city).execute()
        if res.data:
            print(f"SUCCESS: Updated {city} to ID {correct_id}")
        else:
            print(f"SKIPPED: {city} not found.")
    except Exception as e:
        print(f"ERROR updating {city}: {e}")
print("--- Migration Finished ---")
