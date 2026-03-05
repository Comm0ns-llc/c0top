
import asyncio
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

if not url or not key:
    print("Error: SUPABASE_URL or SUPABASE_KEY not set")
    exit(1)

client = create_client(url, key)

# Start of Week (Monday 00:00 JST = Sunday 15:00 UTC)
START_TIME_UTC = "2026-01-18T15:00:00+00:00"

async def restore_all_users():
    print(f"--- Restoring Weekly Scores (Since {START_TIME_UTC}) ---")

    # 1. Fetch all users
    # Adjust Limit if needed. Assuming < 1000 users.
    users_resp = client.table("users").select("user_id, username").execute()
    users = users_resp.data
    
    if not users:
        print("No users found.")
        return

    print(f"Found {len(users)} users. Processing...")

    update_count = 0
    total_restored_points = 0.0

    # 2. Iterate and calculate
    for user in users:
        user_id = user["user_id"]
        username = user["username"]
        
        # Calculate Message Score
        msgs_resp = client.table("messages").select("base_score, nlp_score_multiplier").eq("user_id", user_id).gte("created_at", START_TIME_UTC).execute()
        calc_msg_score = 0.0
        if msgs_resp.data:
            for m in msgs_resp.data:
                calc_msg_score += float(m["base_score"]) * float(m["nlp_score_multiplier"])

        # Calculate Reaction Score (Received)
        # Note: Efficiently fetching reactions is hard without message IDs.
        # Fetching user's message IDs first.
        user_msgs_resp = client.table("messages").select("message_id").eq("user_id", user_id).execute()
        user_msg_ids = [m["message_id"] for m in user_msgs_resp.data]
        
        calc_reaction_score = 0.0
        if user_msg_ids:
            # Check reactions on these messages CREATED this week
            # We fetch user_id to exclude self-reaction
            reactions_resp = client.table("reactions").select("message_id, user_id, weight").gte("created_at", START_TIME_UTC).execute()
            
            # This fetches ALL reactions this week. We filter in memory.
            # This is inefficient if millions of reactions, but likely small for now.
            # Better: fetch reactions for EACH message? Too many requests.
            # Better: filtered list in memory.
            if reactions_resp.data:
                for r in reactions_resp.data:
                    if r["message_id"] in user_msg_ids:
                        if r["user_id"] != user_id: # Exclude self
                            calc_reaction_score += float(r["weight"])
        
        total_score = calc_msg_score + calc_reaction_score
        
        if total_score > 0:
            print(f"Restoring {username}: {total_score:.2f} pts")
            # Update DB
            client.table("users").update({"weekly_score": total_score}).eq("user_id", user_id).execute()
            update_count += 1
            total_restored_points += total_score
        
    print(f"--- Restoration Complete ---")
    print(f"Updated {update_count} users.")
    print(f"Total points restored: {total_restored_points:.2f}")

if __name__ == "__main__":
    asyncio.run(restore_all_users())
