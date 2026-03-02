# c0top Homebrew 配布手順書

この手順書は、`c0top` を Homebrew 経由で一般ユーザーに配布するための実運用フローです。  
目的は「Discord OAuth 認証が通ったユーザーが、`brew install c0top` 後にそのまま TUI を使える状態」を維持することです。

## 0. 完了条件（Definition of Done）

以下をすべて満たせば配布完了です。

1. `brew info Comm0ns-llc/comm0ns/c0top` で期待バージョン（例: `1.0.1`）が表示される
2. クリーン環境で `brew install c0top` が成功する
3. `c0top` 実行時に Discord OAuth 後、`DB LIVE` でTUIが起動する

## 1. 事前準備

作業ディレクトリ:

```bash
cd /Users/tsukuru/Dev/myprojects/comm0ns/comm0ns_discord_bot
```

必要ツール:

```bash
brew --version
git --version
python3 --version
```

## 2. c0top 本体リリース確認

Homebrew Formula は GitHub のタグアーカイブを参照します。  
先に `Comm0ns-llc/c0top` 側で、配布対象タグ（例: `v1.0.1`）が存在していることを確認してください。

参照URL（例）:

```text
https://github.com/Comm0ns-llc/c0top/archive/refs/tags/v1.0.1.tar.gz
```

## 3. sha256 の取得

配布対象 tar.gz の SHA256 を計算します。

```bash
curl -L -o /tmp/c0top-v1.0.1.tar.gz \
  https://github.com/Comm0ns-llc/c0top/archive/refs/tags/v1.0.1.tar.gz

shasum -a 256 /tmp/c0top-v1.0.1.tar.gz
```

出力されたハッシュ値を控えます。

## 4. Homebrew Formula 更新

```bash
cd /Users/tsukuru/Dev/myprojects/comm0ns/comm0ns_discord_bot/homebrew-comm0ns
```

`c0top.rb` で以下を更新:

1. `url` を新バージョンへ
2. `version` を新バージョンへ
3. `sha256` を手順3の値へ
4. OAuthトークン引き渡しがあることを確認  
   `if session and "access_token" in session: os.environ["SUPABASE_AUTH_TOKEN"] = session["access_token"]`

差分確認:

```bash
git diff -- c0top.rb
```

コミット・push:

```bash
git add c0top.rb
git commit -m "chore(release): update c0top formula to v1.0.1"
git push origin main
```

## 5. Tap 更新確認（公開側）

ローカルで tap が古い場合は更新します。

```bash
brew untap comm0ns-llc/comm0ns || true
brew tap Comm0ns-llc/comm0ns
brew update
brew info Comm0ns-llc/comm0ns/c0top
```

`stable` が期待バージョンになっていることを確認します。

## 6. クリーンインストール検証

```bash
brew uninstall c0top || true
brew install Comm0ns-llc/comm0ns/c0top
which c0top
c0top --help
```

次に実起動検証:

```bash
c0top --force-login
```

確認ポイント:

1. ブラウザで Discord OAuth が開始される
2. 認証後にターミナルへ復帰する
3. TUI が起動する
4. 画面右上ステータスが `DB LIVE` になる

## 7. Supabase 側の必須設定

1. Supabase Auth で Discord プロバイダ有効化
2. Redirect URL に以下を登録  
   `http://127.0.0.1:53682/auth/callback`
3. RLS ポリシーが本番に反映済み（`migrations/006_fix_rls.sql` 相当）

## 8. 障害時チェック

### 8.1 `brew info` が旧バージョンのまま

```bash
brew untap comm0ns-llc/comm0ns
brew tap Comm0ns-llc/comm0ns
brew update
brew info Comm0ns-llc/comm0ns/c0top
```

### 8.2 認証は通るが `DB ERROR` / データ0件

確認項目:

1. Homebrew 側 `c0top.rb` が `SUPABASE_AUTH_TOKEN` を C++ 側へ渡しているか
2. C++ 側が `SUPABASE_AUTH_TOKEN` をBearerとして使う実装か
3. Supabase RLS が `authenticated` + community member 条件で正しく適用されているか

## 9. 利用者向けアナウンス文（テンプレ）

```text
c0top を v1.0.1 に更新しました。
以下を実行してください:

brew update
brew upgrade c0top

初回再認証が必要な場合:
c0top --force-login
```

