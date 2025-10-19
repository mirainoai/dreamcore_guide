import os
import sqlite3
import re
from functools import wraps
from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, request, session, redirect, url_for, flash, g
from werkzeug.security import check_password_hash, generate_password_hash

# --- ⚙️ アプリケーション設定 ---
app = Flask(__name__)

# 環境変数からシークレットキーを設定
# 開発環境用にデフォルト値を設定しているが、Renderでは環境変数で上書き推奨
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "your_super_secret_key_fallback")

# セッションCookieの設定
# Renderデプロイ時には、セキュリティのためドメインを指定する必要がある
# 例: .onrender.com を使用 (サブドメイン全体で共有)
# 開発中はNoneまたはコメントアウト
SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN")
if SESSION_COOKIE_DOMAIN:
    app.config['SESSION_COOKIE_DOMAIN'] = SESSION_COOKIE_DOMAIN

# データベースファイルパス
DB_PATH = os.environ.get("DATABASE_PATH", "dreamcore_guide.db")

# Render環境変数 RUN_MIGRATIONS が 'True' の場合のみ、データベース初期化を実行
RUN_MIGRATIONS = os.environ.get("RUN_MIGRATIONS", "False").lower() == 'true'

# 日本時間 (JST) タイムゾーン定義
JST = timezone(timedelta(hours=+9))

# --- 🗄️ データベース接続ヘルパー ---
def get_db():
    # アプリケーションコンテキストにDB接続を保存し、リクエストごとに再利用する
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row # 結果を辞書形式で取得
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# リクエスト完了時にデータベース接続を閉じるように登録
app.teardown_appcontext(close_db)

# --- 🛠️ データベースマイグレーション (初期化) ---
def init_db():
    db = get_db()
    
    # テーブル作成
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            genre TEXT,
            developer TEXT
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_id INTEGER,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (game_id) REFERENCES games (id)
        );
    """)
    db.commit()

    # 初期データを挿入
    # ゲームがまだ存在しない場合のみ挿入
    cursor = db.execute("SELECT COUNT(*) FROM games")
    if cursor.fetchone()[0] == 0:
        db.executescript("""
            INSERT INTO games (title, slug, genre, developer) VALUES 
            ('Echoes of the Void', 'echoes-of-the-void', 'Sci-Fi Horror', 'DreamSoft Studios'),
            ('Neon Dynasty', 'neon-dynasty', 'Cyberpunk RPG', 'PixelForge');
        """)
        db.commit()

if RUN_MIGRATIONS:
    print("--- 💡 Running initial database setup (Migrations)... ---")
    with app.app_context():
        init_db()
    print("--- ✅ Database setup complete! Remember to remove RUN_MIGRATIONS=True from Render! ---")

# --- 🔐 ログイン状態チェックデコレータ ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_id") is None:
            flash("ログインが必要です。", "warning")
            return redirect(url_for("login"))
        g.user_id = session.get("user_id")
        return f(*args, **kwargs)
    return decorated_function

# --- 🚨 デバッグ用: 全データ削除機能 🚨 ---
@app.route('/debug/reset_data', methods=['GET', 'POST'])
def reset_data():
    if not app.debug and os.environ.get("RENDER_EXTERNAL_URL"):
        # 本番環境ではこのデバッグ機能はセキュリティ上、無効にするか、パスワード保護を推奨
        # 今回はデプロイテストのため、一時的に Render でもアクセス可能とする
        pass 
        
    db = get_db()
    if request.method == 'POST':
        # データベースをリセット
        db.executescript("""
            DELETE FROM posts;
            DELETE FROM games;
            DELETE FROM users;
            VACUUM;
        """)
        db.commit()
        # セッションもリセット
        session.clear()
        
        # データベースを再初期化して初期ゲームデータを再挿入
        init_db()
        flash("データベースの全データが削除され、初期化されました。", "success")
        return redirect(url_for('index'))
    
    # GETリクエストの場合、確認画面を表示
    return render_template("reset_data.html")


# --- 🗺️ ルーティング ---

@app.route("/")
def index():
    db = get_db()
    
    # 修正済みクエリ: g.url を削除し、代わりに g.slug を取得
    posts = db.execute("""
        SELECT 
            p.id, p.user_id, p.title, p.content, p.created_at, u.username, 
            g.id AS game_id, g.title AS game_title, g.slug AS game_slug
        FROM 
            posts p
        JOIN users u ON p.user_id = u.id
        LEFT JOIN games g ON p.game_id = g.id
        ORDER BY p.created_at DESC
    """).fetchall()

    # 投稿データを整形
    formatted_posts = []
    for post in posts:
        post_dict = dict(post)
        
        # 投稿日時のフォーマットとJSTへの変換
        dt_utc = datetime.strptime(post_dict['created_at'], '%Y-%m-%d %H:%M:%S')
        dt_jst = dt_utc.replace(tzinfo=timezone.utc).astimezone(JST)
        post_dict['created_at_fmt'] = dt_jst.strftime('%Y年%m月%d日 %H:%M')

        # ゲームへのリンクURLを生成
        if post_dict['game_id'] and post_dict['game_slug']:
            post_dict['game_url'] = url_for('game_detail', game_slug=post_dict['game_slug'], game_id=post_dict['game_id'])
        else:
            post_dict['game_url'] = None

        formatted_posts.append(post_dict)

    return render_template("index.html", posts=formatted_posts)

# --- ユーザー認証 ---

@app.route("/register", methods=["GET", "POST"])
def register():
    """ユーザー登録"""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        if not username:
            flash("ユーザー名を入力してください。", "danger")
            return render_template("register.html")
        if not password:
            flash("パスワードを入力してください。", "danger")
            return render_template("register.html")
        if password != confirmation:
            flash("パスワードが一致しません。", "danger")
            return render_template("register.html", username=username)

        db = get_db()
        
        # パスワードの複雑さチェック (例)
        if len(password) < 8 or not re.search("[a-z]", password) or not re.search("[A-Z]", password) or not re.search("[0-9]", password):
             flash("パスワードは8文字以上で、大文字、小文字、数字をそれぞれ1つ以上含める必要があります。", "danger")
             return render_template("register.html", username=username)
        
        try:
            # ユーザー名をユニークにチェックして挿入
            hash = generate_password_hash(password)
            cursor = db.execute("INSERT INTO users (username, hash) VALUES (?, ?)", (username, hash))
            db.commit()
            
            # 登録成功後、自動的にログインさせる
            session["user_id"] = cursor.lastrowid
            flash("ユーザー登録が完了しました！", "success")
            return redirect(url_for("index"))
            
        except sqlite3.IntegrityError:
            flash("そのユーザー名は既に使用されています。", "danger")
            return render_template("register.html")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    """ユーザーログイン"""
    # 既にログインしている場合はトップへリダイレクト
    if session.get("user_id") is not None:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password:
            flash("ユーザー名とパスワードの両方を入力してください。", "danger")
            return render_template("login.html")

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if user is None or not check_password_hash(user["hash"], password):
            flash("ユーザー名またはパスワードが正しくありません。", "danger")
            return render_template("login.html", username=username)

        # セッションにユーザーIDを保存
        session["user_id"] = user["id"]
        
        flash("ログインしました。", "success")
        return redirect(url_for("index"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    """ユーザーログアウト"""
    session.clear()
    flash("ログアウトしました。", "info")
    return redirect(url_for("index"))

# --- 投稿機能 ---

@app.route("/create", methods=["GET", "POST"])
@login_required
def create_post():
    db = get_db()
    games = db.execute("SELECT id, title, slug FROM games ORDER BY title").fetchall()
    
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        game_id = request.form.get("game_id")

        if not title or not content:
            flash("タイトルと内容の両方を入力してください。", "danger")
            return render_template("create_post.html", games=games)

        # game_idが空の場合はNULLを挿入
        game_id = int(game_id) if game_id and game_id != 'None' else None

        db.execute(
            "INSERT INTO posts (user_id, game_id, title, content) VALUES (?, ?, ?, ?)",
            (g.user_id, game_id, title, content)
        )
        db.commit()
        flash("新しいガイド投稿が作成されました！", "success")
        return redirect(url_for("index"))

    return render_template("create_post.html", games=games)

# --- ゲーム詳細 ---

@app.route("/games/<game_slug>/<int:game_id>")
def game_detail(game_slug, game_id):
    db = get_db()
    
    # ゲーム情報を取得
    game = db.execute(
        "SELECT id, title, genre, developer FROM games WHERE id = ? AND slug = ?", 
        (game_id, game_slug)
    ).fetchone()
    
    if game is None:
        flash("指定されたゲームは見つかりませんでした。", "danger")
        return redirect(url_for("index"))

    # そのゲームに関連する投稿を取得
    posts = db.execute("""
        SELECT 
            p.id, p.title, p.content, p.created_at, u.username
        FROM 
            posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.game_id = ?
        ORDER BY p.created_at DESC
    """, (game_id,)).fetchall()
    
    formatted_posts = []
    for post in posts:
        post_dict = dict(post)
        # 投稿日時のフォーマット
        dt_utc = datetime.strptime(post_dict['created_at'], '%Y-%m-%d %H:%M:%S')
        dt_jst = dt_utc.replace(tzinfo=timezone.utc).astimezone(JST)
        post_dict['created_at_fmt'] = dt_jst.strftime('%Y年%m月%d日 %H:%M')
        formatted_posts.append(post_dict)
    

    return render_template("game_detail.html", game=game, posts=formatted_posts)


# --- 実行 ---
if __name__ == "__main__":
    # 開発環境でのみ動作する設定
    # RenderではGunicornが起動するため、このブロックは実行されない
    if RUN_MIGRATIONS:
        print("💡 開発環境: データベースを初期化中...")
        with app.app_context():
            init_db()
        print("✅ 開発環境: データベース初期化完了")
        
    app.run(debug=True, port=8000)
