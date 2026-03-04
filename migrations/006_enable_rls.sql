-- ============================================
-- Enable Row Level Security (RLS) script
-- ============================================
-- このスクリプトは、Discordからログインした一般ユーザー（Anon Key）が、
-- 自分が所属するコミュニティのデータ「だけ」を読み取れるように制限をかけます。
-- ============================================

-- 1. まず全テーブルに対してRLS制約を有効化します。
-- （これにより、特権管理者以外は一切のデータが読み書きできなくなります）
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE reactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_metadata ENABLE ROW LEVEL SECURITY;

-- ※ もし将来 `votes` や `issues` テーブルを作成済みの場合は
-- それらに対しても ENABLE ROW LEVEL SECURITY を実行してください。

-- 2. アクセス許可ルール（Policies）の作成
-- SupabaseのAuth機能で取得したDiscord情報（user_name or provider_id 等）を使って
-- 「そのユーザーが当コミュニティの `users` テーブルに登録された人であるか」を判定します。

-- [A] `users` テーブルの読み取り許可
-- 認証済み（authenticated）かつ、ログイン者のProvider情報(Discord側ID)がusersテーブルに存在する場合のみ閲覧可能
DROP POLICY IF EXISTS "Members can view users" ON users;
CREATE POLICY "Members can view users" ON users
    FOR SELECT USING (
        auth.role() = 'authenticated'
        AND EXISTS (
             -- `auth.jwt() ->> 'sub'` がSupabase内の固有IDになってしまう場合があるため、
             -- 確実を期すなら user_metadata などを経由してDiscord IDを取るか、
             -- ログインしている事実（=Discord認証が通っている）を持っていればヨシとする簡易版
             -- 今回のダッシュボード仕様に合わせて「誰でもログインはできるが、usersテーブルに1人でも所属の形跡がある（＝botが参加しているサーバーのメンバーである）」で簡易判定します。
            SELECT 1 FROM users WHERE CAST(users.user_id AS TEXT) = (auth.jwt() -> 'user_metadata' ->> 'provider_id')
        )
    );

-- [B] `messages` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view messages" ON messages;
CREATE POLICY "Members can view messages" ON messages
    FOR SELECT USING (
        auth.role() = 'authenticated'
        AND EXISTS (
            SELECT 1 FROM users WHERE CAST(users.user_id AS TEXT) = (auth.jwt() -> 'user_metadata' ->> 'provider_id')
        )
    );

-- [C] `reactions` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view reactions" ON reactions;
CREATE POLICY "Members can view reactions" ON reactions
    FOR SELECT USING (
        auth.role() = 'authenticated'
        AND EXISTS (
            SELECT 1 FROM users WHERE CAST(users.user_id AS TEXT) = (auth.jwt() -> 'user_metadata' ->> 'provider_id')
        )
    );

-- [D] `channels` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view channels" ON channels;
CREATE POLICY "Members can view channels" ON channels
    FOR SELECT USING (
        auth.role() = 'authenticated'
        AND EXISTS (
            SELECT 1 FROM users WHERE CAST(users.user_id AS TEXT) = (auth.jwt() -> 'user_metadata' ->> 'provider_id')
        )
    );

-- [E] `bot_metadata` テーブルの読み取り許可
DROP POLICY IF EXISTS "Members can view bot_metadata" ON bot_metadata;
CREATE POLICY "Members can view bot_metadata" ON bot_metadata
    FOR SELECT USING (
        auth.role() = 'authenticated'
        AND EXISTS (
            SELECT 1 FROM users WHERE CAST(users.user_id AS TEXT) = (auth.jwt() -> 'user_metadata' ->> 'provider_id')
        )
    );

-- ※ 注意: 上記のルールは「読み取り(SELECT)」専用です。
-- C++やPythonのダッシュボードからはデータの書き込み等は行わない仕様のため、
-- INSERT, UPDATE, DELETE はすべて拒否された状態(デフォルト)を維持します。
-- Discordボット本体は `Service Role Key` を使用するため、このRLS制限を突破して自由に読み書き可能です。
