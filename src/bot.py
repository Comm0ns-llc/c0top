"""
Discord Bot Main Module
メインのBotクラスとイベントハンドラー
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone

from .config import config, validate_config, EmbedColors
from .database import DatabaseError
from .storage import storage
from .scoring import scoring_engine
from .nlp_analyzer import nlp_analyzer

if TYPE_CHECKING:
    from discord import Message
    from discord import RawReactionActionEvent

# ロギング設定
logging.basicConfig(
    level=logging.DEBUG if config.debug_mode else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

DEPLOY_NOTICE_METADATA_KEY = "last_deploy_notice_version"


class QualityBot(commands.Bot):
    """
    Discord Quality Scoring Bot
    
    メッセージの品質を評価し、ランキング化するBot
    """
    
    def __init__(self) -> None:
        """Botを初期化"""
        intents = discord.Intents.default()
        # メッセージ内容は使わないが、リアクション加点のためreactionsは必要
        # on_message の受信・デバッグを安定させるため有効化（内容は保存しない）
        intents.message_content = True
        intents.reactions = True
        intents.members = True # Ensure members intent is also on
        
        super().__init__(
            "!", # command_prefix (positional)
            intents=intents,
            application_id=config.discord.application_id or None
        )
    
    async def setup_hook(self) -> None:
        """Bot起動時の初期化処理"""
        logger.info("Setting up bot...")
        
        # スラッシュコマンドを同期
        # スラッシュコマンドを同期
        
        # 以前のバージョンで登録された「ギルド専用コマンド」が残っていると重複して表示されるため
        # 明示的にギルド専用コマンドを削除（クリア）する
        if config.discord.guild_id:
            guild = discord.Object(id=int(config.discord.guild_id))
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Cleared guild commands for: {config.discord.guild_id}")

        # グローバル同期（これだけを正とする）
        await self.tree.sync()
        logger.info("Slash commands synced globally")
    
    async def on_ready(self) -> None:
        """Bot準備完了時のイベント"""
        if self.user:
            logger.info(f"Logged in as {self.user.name} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")
        logger.info(f"Discord.py Version: {discord.__version__}")
        logger.info(f"Intents: message_content={self.intents.message_content}, members={self.intents.members}, presences={self.intents.presences}")
        
        # ステータスを設定
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="コミュニティの品質 📊"
            )
        )
        
        # チャンネル情報を同期
        logger.info("Syncing channel information...")
        channel_count = 0
        for guild in self.guilds:
            for channel in guild.channels:
                # テキスト、ボイス、ステージ、フォーラムなどを対象にする
                if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)):
                    await storage.upsert_channel(
                        channel_id=channel.id,
                        name=channel.name,
                        channel_type=str(channel.type)
                    )
                    channel_count += 1
        logger.info(f"Synced {channel_count} channels across {len(self.guilds)} guilds")

        # デプロイ更新通知（同じバージョンでは1回だけ）
        await self._notify_deploy_update_once()

        # 週間リセットタスクを開始
        if not self.check_weekly_reset.is_running():
            self.check_weekly_reset.start()
    
    async def close(self) -> None:
        """Bot終了時のクリーンアップ"""
        logger.info("Shutting down bot...")
        self.check_weekly_reset.cancel()
        await super().close()

    def _release_fingerprint_from_files(self) -> str | None:
        """
        環境変数でリリースIDが取れない場合のフォールバック。
        同一コードでは同一値を返すため、再起動時の重複通知を防げる。
        """
        root = Path(__file__).resolve().parents[1]
        targets = [
            "main.py",
            "src/bot.py",
            "src/storage.py",
            "src/database.py",
            "requirements.txt",
        ]
        h = hashlib.sha256()
        used = 0
        for rel in targets:
            p = root / rel
            if not p.exists():
                continue
            try:
                h.update(rel.encode("utf-8"))
                h.update(b"\0")
                h.update(p.read_bytes())
                h.update(b"\0")
                used += 1
            except OSError:
                continue
        if used == 0:
            return None
        return f"code:{h.hexdigest()[:16]}"

    def _get_release_version(self) -> str | None:
        # 明示指定を最優先。CI/CD から与える想定。
        for key in ("BOT_RELEASE_VERSION", "RELEASE_VERSION", "SOURCE_VERSION"):
            value = os.getenv(key, "").strip()
            if value:
                return value

        # Fly.io runtime 情報があれば使用。
        for key in ("FLY_IMAGE_REF", "FLY_MACHINE_VERSION"):
            value = os.getenv(key, "").strip()
            if value:
                return value

        # 最後の手段としてコード指紋を使用。
        return self._release_fingerprint_from_files()

    async def _notify_deploy_update_once(self) -> None:
        if not config.discord.notification_channel_id:
            return

        release_version = self._get_release_version()
        if not release_version:
            logger.info("Skip deploy notice: release version is unavailable.")
            return

        last_version = await storage.get_metadata(DEPLOY_NOTICE_METADATA_KEY)
        if last_version == release_version:
            return

        claimed = await storage.compare_and_set_metadata(
            key=DEPLOY_NOTICE_METADATA_KEY,
            expected_value=last_version,
            new_value=release_version,
        )
        if not claimed:
            logger.info("Deploy notice already sent by another worker for version=%s", release_version)
            return

        try:
            channel_id = int(config.discord.notification_channel_id)
            channel = self.get_channel(channel_id)
            if not channel:
                logger.warning(f"Notification channel {channel_id} not found")
                return

            embed = discord.Embed(
                title="✅ Botアップデート反映",
                description=(
                    "新しいバージョンを反映しました。\n"
                    f"release: `{release_version[:80]}`"
                ),
                color=EmbedColors.INFO,
                timestamp=datetime.now(timezone.utc),
            )
            await channel.send(embed=embed)
            logger.info("Sent deploy update notice to channel %s (release=%s)", channel_id, release_version)
        except ValueError:
            logger.error("Invalid notification channel ID")
        except Exception as e:
            logger.error(f"Failed to send deploy update notice: {e}")

    @tasks.loop(minutes=60)
    async def check_weekly_reset(self) -> None:
        """
        週間スコアリセットのチェック
        
        毎週月曜日の0:00 (UTC) 頃に実行されることを想定
        """
        try:
            # 現在の週番号を取得 (ISO 8601: 月曜始まり)
            # YYYY-Www 形式 (例: 2023-W42)
            now = datetime.now(timezone.utc)
            current_week = now.strftime("%Y-W%V")
            
            # DBから「最後にリセットした週」を取得
            last_reset_week = await storage.get_metadata("last_weekly_reset_week")
            
            if last_reset_week != current_week:
                logger.info(f"New week detected: {current_week} (Last: {last_reset_week})")
                
                # リセットを実行
                success = await storage.reset_weekly_scores()
                
                if success:
                    # メタデータをCAS更新できたインスタンスだけ通知を送る
                    claimed = await storage.compare_and_set_metadata(
                        key="last_weekly_reset_week",
                        expected_value=last_reset_week,
                        new_value=current_week,
                    )
                    if not claimed:
                        logger.info(
                            "Weekly reset already finalized by another worker (week=%s). Skip notification.",
                            current_week,
                        )
                        return

                    logger.info("Weekly leaderboard reset completed.")
                    
                    # 通知チャンネルがあれば通知を送る
                    if config.discord.notification_channel_id:
                        try:
                            channel_id = int(config.discord.notification_channel_id)
                            channel = self.get_channel(channel_id)
                            if channel:
                                embed = discord.Embed(
                                    title="🔄 週間リーダーボード リセット",
                                    description=(
                                        "新しい週が始まりました！\n"
                                        "週間スコアがリセットされました。\n"
                                        "今週も上位を目指して頑張りましょう！ 🚀"
                                    ),
                                    color=EmbedColors.INFO,
                                    timestamp=now
                                )
                                await channel.send(embed=embed)
                                logger.info(f"Sent reset notification to channel {channel_id}")
                            else:
                                logger.warning(f"Notification channel {channel_id} not found")
                        except ValueError:
                            logger.error("Invalid notification channel ID")
                        except Exception as e:
                            logger.error(f"Failed to send reset notification: {e}")
                else:
                    logger.error("Failed to reset weekly leaderboard.")
        
        except Exception as e:
            logger.error(f"Error in check_weekly_reset: {e}")

    @check_weekly_reset.before_loop
    async def before_check_weekly_reset(self) -> None:
        """ループ開始前の待機"""
        await self.wait_until_ready()

    async def on_message(self, message: Message) -> None:
        """
        メッセージ受信時のイベントハンドラー
        
        1. Botのメッセージは無視
        2. ユーザー情報をupsert
        3. メッセージを保存（発言=3pt）
        4. ユーザースコアを +3 する
        """
        # Botのメッセージは無視
        if message.author.bot:
            return
        
        # DMは無視（サーバーのみ対象）
        if not message.guild:
            return
        
        try:
            logger.info(
                "on_message received: guild=%s channel=%s author=%s",
                message.guild.id,
                message.channel.id,
                message.author.id,
            )
            # ユーザー情報をupsert
            await storage.upsert_user(
                user_id=message.author.id,
                username=message.author.display_name
            )
            
            # NLP分析を実行
            # Message Content Intentが必要だが、内容自体は保存しない（分析にのみ使用）
            nlp_multiplier = await nlp_analyzer.analyze(message.content)

            # メッセージを保存（初期スコア）
            base_score = config.scoring.BASE_SCORE_PER_MESSAGE

            # メッセージ内容を保存（分析・表示用）
            content = message.content
            
            # チャンネル情報をupsert
            await storage.upsert_channel(
                channel_id=message.channel.id,
                name=message.channel.name,
                channel_type=str(message.channel.type)
            )

            message_record = await storage.insert_message(
                message_id=message.id,
                user_id=message.author.id,
                channel_id=message.channel.id,
                guild_id=message.guild.id,
                content=content,
                nlp_score_multiplier=nlp_multiplier,
                base_score=base_score
            )
            
            if message_record:
                # 計算された合計スコアをユーザーに加算
                initial_score = float(message_record["total_score"])
                await storage.update_user_score(message.author.id, initial_score)

                logger.info(
                    "score updated: author=%s +%s (multiplier=%.1f)",
                    message.author.id,
                    initial_score,
                    nlp_multiplier,
                )
            
            logger.debug(f"Message processed: {message.id} from {message.author}")
            
        except DatabaseError as e:
            logger.error(f"Database error processing message: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
        
        # コマンドの処理を継続
        await self.process_commands(message)

    async def on_raw_reaction_add(self, payload: RawReactionActionEvent) -> None:
        """リアクション1つにつき1ptをメッセージ投稿者に加算"""
        # Bot自身のリアクションは無視
        if self.user and payload.user_id == self.user.id:
            return

        try:
            message = await storage.get_message(payload.message_id)
            if not message:
                return

            # 自分のメッセージへの自分のリアクションは無視
            if int(message["user_id"]) == int(payload.user_id):
                return

            emoji_name = str(payload.emoji.name) if payload.emoji.name else str(payload.emoji)

            exists = await storage.check_reaction_exists(
                payload.message_id,
                payload.user_id,
                emoji_name,
            )
            if exists:
                return

            weight = float(scoring_engine.calculate_reaction_weight(emoji_name))

            await storage.insert_reaction(
                message_id=payload.message_id,
                user_id=payload.user_id,
                reaction_type=emoji_name,
                weight=weight,
            )

            await storage.update_message_reaction_score(payload.message_id, weight)
            await storage.update_user_score(int(message["user_id"]), weight)

        except DatabaseError as e:
            logger.error(f"Database error processing reaction: {e}")
        except Exception as e:
            logger.error(f"Error processing reaction: {e}")


# Botインスタンスを作成
bot = QualityBot()


# ============================================
# Slash Commands
# ============================================

@bot.tree.command(name="rank", description="自分のランキングとスコア内訳を表示")
async def rank_command(interaction: discord.Interaction) -> None:
    """
    /rank コマンド
    
    自分の順位とスコア内訳を表示
    """
    await interaction.response.defer(thinking=True)
    
    try:
        user_id = interaction.user.id
        
        # ユーザー情報を取得
        user = await storage.get_user(user_id)
        if not user:
            embed = discord.Embed(
                title="❌ データが見つかりません",
                description="まだメッセージを送信していないようです。\nメッセージを送信するとスコアが記録されます！",
                color=EmbedColors.WARNING
            )
            await interaction.followup.send(embed=embed)
            return
        
        # 順位を取得
        rank_info = await storage.get_user_rank(user_id)
        rank = rank_info[0] if rank_info else None
        total_users = rank_info[1] if rank_info else None
        
        # メッセージ統計を取得（Active Scoreのみ利用）
        stats = await storage.get_user_messages_stats(user_id)
        breakdown = scoring_engine.calculate_user_total_score(stats)
        
        # Embedを作成
        embed = discord.Embed(
            title=f"📊 {interaction.user.display_name} のスコア",
            color=EmbedColors.GOLD if rank and rank <= 3 else EmbedColors.INFO
        )
        
        # 順位
        if rank and total_users:
            medal = scoring_engine._get_rank_medal(rank)
            embed.add_field(
                name="🏆 順位",
                value=f"{medal} **{rank}位** / {total_users}人中",
                inline=False
            )
        
        # スコア（発言 + リアクション）
        embed.add_field(
            name="📝 スコア (発言数)",
            value=f"{breakdown.base_score:.1f}pt",
            inline=True
        )
        embed.add_field(
            name="⭐ スコア (リアクション)",
            value=f"{breakdown.impact_score:.1f}pt",
            inline=True
        )
        embed.add_field(
            name="📈 合計スコア",
            value=f"**{breakdown.total_score:.1f}pt**",
            inline=False
        )
        embed.add_field(
            name="📅 週間スコア",
            value=f"{float(user['weekly_score']):.1f}pt",
            inline=True
        )
        
        # 統計情報
        embed.add_field(
            name="📊 統計",
            value=f"総メッセージ数: {stats['total_messages']}",
            inline=False
        )
        
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text="💡 質の高い発言でスコアアップ！")
        
        await interaction.followup.send(embed=embed)
        
    except DatabaseError as e:
        logger.error(f"Database error in rank command: {e}")
        embed = discord.Embed(
            title="❌ エラー",
            description="データの取得中にエラーが発生しました。",
            color=EmbedColors.ERROR
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in rank command: {e}")
        embed = discord.Embed(
            title="❌ エラー",
            description="予期しないエラーが発生しました。",
            color=EmbedColors.ERROR
        )
        await interaction.followup.send(embed=embed)


@bot.tree.command(name="leaderboard", description="上位10名のランキングを表示")
@app_commands.describe(weekly="週間ランキングを表示する場合はTrue")
async def leaderboard_command(
    interaction: discord.Interaction,
    weekly: bool = False
) -> None:
    """
    /leaderboard コマンド
    
    上位10名のランキングを表示
    """
    await interaction.response.defer(thinking=True)
    
    try:
        # リーダーボードを取得
        leaderboard = await storage.get_leaderboard(limit=10, weekly=weekly)
        
        if not leaderboard:
            embed = discord.Embed(
                title="📊 リーダーボード",
                description="まだランキングデータがありません。\nメッセージを送信してスコアを獲得しましょう！",
                color=EmbedColors.WARNING
            )
            await interaction.followup.send(embed=embed)
            return
        
        # タイトル
        title = "🏆 週間ランキング TOP10" if weekly else "🏆 累計ランキング TOP10"
        
        # Embedを作成
        embed = discord.Embed(
            title=title,
            color=EmbedColors.GOLD
        )
        
        # ランキングエントリを構築
        entries: list[str] = []
        for entry in leaderboard:
            score = entry["weekly_score"] if weekly else entry["current_score"]
            user_id = int(entry["user_id"])
            username = entry["username"]
            
            # サーバーから最新の表示名を取得を試みる
            if interaction.guild:
                member = interaction.guild.get_member(user_id)
                if member:
                    username = member.display_name
            
            line = scoring_engine.format_leaderboard_entry(
                rank=entry["rank"],
                username=username,
                score=score,
                weekly=weekly
            )
            entries.append(line)
        
        embed.description = "\n".join(entries)
        
        # 自分の順位を追加
        rank_info = await storage.get_user_rank(interaction.user.id)
        if rank_info:
            rank, total = rank_info
            if rank > 10:
                embed.add_field(
                    name="📍 あなたの順位",
                    value=f"**{rank}位** / {total}人中",
                    inline=False
                )
        
        embed.set_footer(text="💡 /rank で自分の詳細スコアを確認できます")
        
        await interaction.followup.send(embed=embed)
        
    except DatabaseError as e:
        logger.error(f"Database error in leaderboard command: {e}")
        embed = discord.Embed(
            title="❌ エラー",
            description="データの取得中にエラーが発生しました。",
            color=EmbedColors.ERROR
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in leaderboard command: {e}")
        embed = discord.Embed(
            title="❌ エラー",
            description="予期しないエラーが発生しました。",
            color=EmbedColors.ERROR
        )
        await interaction.followup.send(embed=embed)


# ============================================
# Main Entry Point
# ============================================

def main() -> None:
    """Botを起動"""
    # 設定を検証
    errors = validate_config()
    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        raise SystemExit("Configuration validation failed")
    
    logger.info("Starting Discord Quality Bot...")
    bot.run(config.discord.bot_token)


if __name__ == "__main__":
    main()
