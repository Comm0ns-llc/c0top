import os
import sys
from supabase import create_client, Client
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
with open(env_path) as f:
    env = dict(line.strip().split("=", 1) for line in f if "=" in line and not line.startswith("#"))
supabase: Client = create_client(env["SUPABASE_URL"], env["SUPABASE_KEY"])
res = supabase.table("users").select("*").eq("user_id", "859444280808701982").execute()
print(f"User found: {len(res.data) > 0}")
if res.data: print(res.data)
