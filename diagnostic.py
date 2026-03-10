import supabase
print(f"Supabase version: {supabase.__version__}")
from supabase import create_client, Client, ClientOptions
print("ClientOptions available" if "ClientOptions" in globals() else "ClientOptions NOT available")

try:
    from supabase import create_async_client
    print("create_async_client available")
except ImportError:
    print("create_async_client NOT available")

import httpx
print(f"HTTPX version: {httpx.__version__}")
