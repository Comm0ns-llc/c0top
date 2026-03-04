-- ============================================
-- Fix: Enable Row Level Security (RLS) script
-- ============================================
-- 先ほどのスクリプトでは、全員に対して `users` テーブルの読み取りを
-- 制限した結果、ポリシー内の `EXISTS(SELECT 1 FROM users...)` という
-- 「自分がコミュニティメンバーかどうか確認する処理」すらもブロックされていました。
-- これを解決するため、特権（SECURITY DEFINER）を持った確認用関数を作成します。
-- ============================================

-- 1. まず、自分が参加者かどうかをチェックする専用の関数を作成します。
-- SECURITY DEFINER をつけることで、この関数の中だけは「全権限」で users テーブルを見に行けます。
CREATE OR REPLACE FUNCTION is_community_member()
RETURNS BOOLEAN AS $$
DECLARE
    discord_id TEXT;
    is_member BOOLEAN;
BEGIN
    -- JWTからログイン元のDiscord IDを取得
    discord_id := auth.jwt() -> 'user_metadata' ->> 'provider_id';
    
    -- IDが空なら即NG
    IF discord_id IS NULL THEN
        RETURN FALSE;
    END IF;

    -- users テーブルにそのIDが存在するかチェック
    SELECT EXISTS (
        SELECT 1 FROM users WHERE CAST(users.user_id AS TEXT) = discord_id
    ) INTO is_member;
    
    RETURN is_member;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- 2. アクセス許可ルール（Policies）を、先ほどの関数を使うように上書きします。

-- [A] `users` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view users" ON users;
CREATE POLICY "Members can view users" ON users
    FOR SELECT USING (
        auth.role() = 'authenticated' AND is_community_member()
    );

-- [B] `messages` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view messages" ON messages;
CREATE POLICY "Members can view messages" ON messages
    FOR SELECT USING (
        auth.role() = 'authenticated' AND is_community_member()
    );

-- [C] `reactions` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view reactions" ON reactions;
CREATE POLICY "Members can view reactions" ON reactions
    FOR SELECT USING (
        auth.role() = 'authenticated' AND is_community_member()
    );

-- [D] `channels` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view channels" ON channels;
CREATE POLICY "Members can view channels" ON channels
    FOR SELECT USING (
        auth.role() = 'authenticated' AND is_community_member()
    );

-- [E] `bot_metadata` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view bot_metadata" ON bot_metadata;
CREATE POLICY "Members can view bot_metadata" ON bot_metadata
    FOR SELECT USING (
        auth.role() = 'authenticated' AND is_community_member()
    );
