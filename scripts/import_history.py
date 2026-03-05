"""
Discord Chat Exporter Import Script

DiscordChatExporter (https://github.com/Tyrrrz/DiscordChatExporter) で
出力されたJSONファイルを読み込み、Supabaseにインポートするスクリプト。

Usage:
    python tools/import_history.py <json_file_path>

Dependencies:
    - python-dotenv
    - supabase
"""
import json
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

# プロジェクトルートの.envを読み込む
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

# 設定
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BASE_SCORE = 3.0  # 過去ログは一律3点 (NLP分析はコストがかかるため省略)

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL and SUPABASE_KEY must be set in .env")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

async def import_history(file_path: str):
    """JSONファイルを読み込んでインポート"""
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File not found: {file_path}")
        return

    print(f"Reading {file_path}...")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    messages = data.get("messages", [])
    guild_id = data.get("guild", {}).get("id")
    channel_id = data.get("channel", {}).get("id")

    if not guild_id or not channel_id:
        print("Error: Invalid JSON format (missing guild or channel info)")
        return

    print(f"Found {len(messages)} messages. Starting import...")
    
    # ユーザーごとのスコア集計用
    user_scores = {}
    users_to_upsert = {}

    imported_count = 0
    skipped_count = 0

    # 500件ずつバッチ処理
    BATCH_SIZE = 500
    
    for i in range(0, len(messages), BATCH_SIZE):
        batch = messages[i:i + BATCH_SIZE]
        message_records = []
        
        for msg in batch:
            author = msg.get("author", {})
            user_id = author.get("id")
            username = author.get("name") # DiscordChatExporterのJSONにはdiscriminatorが含まれる場合があるが、まずはnameを使う
            
            if not user_id or author.get("isBot"):
                skipped_count += 1
                continue

            # ユーザー情報を記録
            if user_id not in users_to_upsert:
                users_to_upsert[user_id] = {
                    "user_id": int(user_id),
                    "username": username,
                    # "avatar_url": author.get("avatarUrl"), # DBスキーマに無いため一時的に無効化
                    "updated_at": datetime.now().isoformat()
                }

            # メッセージレコード作成
            ts_str = msg.get("timestamp") # ISO format expected
            base_score = float(BASE_SCORE)
            nlp_multiplier = 1.0
            total_score = base_score * nlp_multiplier
            
            record = {
                "message_id": int(msg["id"]),
                "user_id": int(user_id),
                "channel_id": int(channel_id),
                "guild_id": int(guild_id),
                "content": msg.get("content"), # 会話内容を保存
                "base_score": base_score,
                "nlp_score_multiplier": nlp_multiplier, # 過去ログは分析なし
                "total_score": total_score,
                "created_at": ts_str
            }
            message_records.append(record)
            
            # スコア集計
            current = user_scores.get(user_id, 0.0)
            user_scores[user_id] = current + float(BASE_SCORE)

        # ユーザー情報のUpsert (バッチごとに行うか、最後に行うか。今回はバッチごとに確実に)
        if users_to_upsert:
            try:
                # ユーザー登録（既存なら無視、ただし名前更新した方がいいかもだが、今回はシンプルに）
                supabase.table("users").upsert(
                    list(users_to_upsert.values()), 
                    on_conflict="user_id"
                ).execute()
                users_to_upsert.clear() # 次のバッチのためにクリア
            except Exception as e:
                print(f"Error upserting users: {e}")

        # メッセージの一括挿入
        if message_records:
            try:
                supabase.table("messages").upsert(
                    message_records,
                    on_conflict="message_id",
                    ignore_duplicates=True 
                ).execute()
                imported_count += len(message_records)
                print(f"Imported batch {i} - {i + len(batch)} / {len(messages)}")
            except Exception as e:
                print(f"Error importing messages batch: {e}")

    # 最終的なスコア更新
    print("Updating user scores...")
    for uid, score in user_scores.items():
        try:
            # 現在のスコアを取得
            res = supabase.table("users").select("current_score").eq("user_id", uid).execute()
            if res.data:
                current = float(res.data[0]["current_score"] or 0)
                # total_score カラムは存在しない可能性があるため更新しない (current_scoreのみ)
                
                supabase.table("users").update({
                    "current_score": current + score,
                    "weekly_score": float(res.data[0].get("weekly_score", 0)) + score # 週間スコアも加算しておく
                }).eq("user_id", uid).execute()
                
        except Exception as e:
            print(f"Error updating score for user {uid}: {e}")

    print("===========================================")
    print(f"Import Finished!")
    print(f"Total Messages Processed: {len(messages)}")
    print(f"Imported: {imported_count}")
    print(f"Skipped (Bots/Error): {skipped_count}")
    print("===========================================")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/import_history.py <json_file_path> [json_file_path2 ...]")
        sys.exit(1)
    
    # 全ての引数を処理
    files = sys.argv[1:]
    
    async def run_all():
        for f in files:
            await import_history(f)
            
    asyncio.run(run_all())
