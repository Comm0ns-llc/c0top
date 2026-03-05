
import asyncio
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

# 環境変数をロード
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL or SUPABASE_KEY not not found in .env")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

async def recalc_scores():
    print("Starting score recalculation...")
    
    # 全ユーザーを取得
    # 一度に取得する数が多すぎる場合はページネーションが必要だが、
    # 今回は簡易的にlimitを大きく設定（例: 1000人）
    print("Fetching users...")
    users_res = supabase.table("users").select("user_id, username").limit(1000).execute()
    users = users_res.data
    
    if not users:
        print("No users found.")
        return

    print(f"Found {len(users)} users. Calculating scores...")

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    week_ago_iso = week_ago.isoformat()

    updated_count = 0

    for user in users:
        user_id = user["user_id"]
        username = user.get("username", "Unknown")
        
        try:
            # そのユーザーの全メッセージを取得
            # 統計情報のみ必要なため、メッセージ内容自体は不要だが、
            # total_scoreのカラムを集計する必要がある。
            # Supabase(PostgREST)では集計関数を直接叩くのが少し面倒なため、
            # 生データを取得してPython側で計算する（データ量が多いと遅くなるため注意）
            # 最適化: .dataset() 等を使うか、RPCを作るのが本来は良い。
            
            # 集計用変数
            current_score = 0.0
            weekly_score = 0.0
            
            # メッセージをページネーションして全件取得
            limit = 1000
            offset = 0
            has_more = True
            
            while has_more:
                print(f"  Fetching messages offset={offset}...")
                res = supabase.table("messages").select("message_id, base_score, nlp_score_multiplier, total_score, created_at").eq("user_id", user_id).range(offset, offset + limit - 1).execute()
                batch = res.data
                
                if not batch:
                    has_more = False
                    break
                    
                if len(batch) < limit:
                    has_more = False
                
                offset += len(batch)

                for m in batch:
                    score = float(m.get("total_score", 0))
                    
                    # スコアが0の場合、自動修復を試みる
                    # (以前のインポートでtotal_scoreが欠損している可能性があるため)
                    if score == 0:
                        base = float(m.get("base_score", 0))
                        nlp = float(m.get("nlp_score_multiplier", 1.0))
                        calc_score = base * nlp
                        if calc_score > 0:
                            # print(f"    Repairing score for msg {m['message_id']}: 0 -> {calc_score}")
                            score = calc_score
                            # DB書き戻しは重くなるので今回は省略し、集計値のみ正す
                            # (次回インポート等で直ることを期待、あるいは別途修正スクリプト)
                            
                    current_score += score
                    
                    # created_at の判定
                    created_at_str = m.get("created_at")
                    if created_at_str:
                        try:
                            msg_time = datetime.fromisoformat(created_at_str)
                            if msg_time.tzinfo is not None:
                                msg_time_utc = msg_time.astimezone(timezone.utc)
                            else:
                                msg_time_utc = msg_time.replace(tzinfo=timezone.utc)
                                
                            if msg_time_utc >= week_ago:
                                weekly_score += score
                        except ValueError:
                            continue
            
            print(f"User: {username} ({user_id}) -> Total: {current_score:.1f}, Weekly: {weekly_score:.1f}")

            # Usersテーブルを更新
            supabase.table("users").update({
                "current_score": current_score,
                "weekly_score": weekly_score,
                "updated_at": now.isoformat()
            }).eq("user_id", user_id).execute()
            
            updated_count += 1

        except Exception as e:
            print(f"Error processing user {user_id}: {e}")

    print("===========================================")
    print(f"Recalculation Finished! Updated {updated_count} users.")
    print("===========================================")

if __name__ == "__main__":
    asyncio.run(recalc_scores())
