import os
import sys
from supabase import create_client, Client

env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if not os.path.exists(env_path):
    print("No .env found", file=sys.stderr)
    sys.exit(1)

with open(env_path) as f:
    env = dict(line.strip().split("=", 1) for line in f if "=" in line and not line.startswith("#"))

url = env.get("SUPABASE_URL")
key = env.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

result = supabase.table("users").select("*").limit(1).execute()
print(f"Service Role Result: {result.data}")
