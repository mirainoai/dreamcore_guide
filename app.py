import os
import sqlite3
import re
from functools import wraps
from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, request, session, redirect, url_for, flash, g
from werkzeug.security import check_password_hash, generate_password_hash

# --- ⚙️ アプリケーション設定 ---
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "your_super_secret_key_fallback")
SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN")
if SESSION_COOKIE_DOMAIN:
    app.config['SESSION_COOKIE_DOMAIN'] = SESSION_COOKIE_DOMAIN

DB_PATH = os.environ.get("DATABASE_PATH", "dreamcore_guide.db")
RUN_MIGRATIONS = os.environ.get("RUN_MIGRATIONS", "False").lower() == 'true'
JST = timezone(timedelta(hours=+9))

# --- 🗄️ データベース接続ヘルパー ---
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

app.teardown_appcontext(close_db)

# --- 🛠️ データベースマイグレーション (初期化) ---
def init_db():
    db = get_db()
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
    print("--- ✅ Database setup complete! ---")

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

# --- 🗺️ ルーティング ---

@app.route("/")
def index():
    try:
        db = get_db()
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

        formatted_posts = []
        for post in posts:
            post_dict = dict(post)
            dt_utc = datetime.strptime(post_dict['created_at'], '%Y-%m-%d %H:%M:%S')
            dt_jst = dt_utc.replace(tzinfo=timezone.utc).astimezone(JST)
            post_dict['created_at_fmt'] = dt_jst.strftime('%Y年%m月%d日 %H:%M')
            if post_dict['game_id'] and post_dict['game_slug']:
                post_dict['game_url'] = url_for('game_detail', game_slug=post_dict['game_slug'], game_id=post_dict['game_id'])
            else:
                post_dict['game_url'] = None
            formatted_posts.append(post_dict)

    except sqlite3.Error as e:
        flash(f"データベースエラーが発生しました: {str(e)}", "danger")
        formatted_posts = []

    return render_template("index.html", posts=formatted_posts)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        if not username or not password or password != confirmation:
            flash("入力が不完全またはパスワードが一致しません。", "danger")
            return render_template("register.html", username=username)

        try:
            db = get_db()
            if len(password) < 8 or not re.search("[a-zA-Z0-9]", password):
                flash("パスワードは8文字以上で、文字と数字を含めてください。", "danger")
                return render_template("register.html", username=username)

            hash_val = generate_password_hash(password)
            cursor = db.execute("INSERT INTO users (username, hash) VALUES (?, ?)", (username, hash_val))
            db.commit()
            session["user_id"] = cursor.lastrowid
            flash("登録完了！", "success")
            return redirect(url_for("index"))

        except sqlite3.IntegrityError:
            flash("ユーザー名が既に使用されています。", "danger")
        except sqlite3.Error as e:
            flash(f"エラー: {str(e)}", "danger")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password:
            flash("ユーザー名とパスワードを入力してください。", "danger")
            return render_template("login.html")

        try:
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user["hash"], password):
                session["user_id"] = user["id"]
                flash("ログイン成功！", "success")
                return redirect(url_for("index"))
            flash("ユーザー名またはパスワードが間違っています。", "danger")

        except sqlite3.Error as e:
            flash(f"エラー: {str(e)}", "danger")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。", "info")
    return redirect(url_for("index"))

@app.route("/create", methods=["GET", "POST"])
@login_required
def create_post():
    db = get_db()
    games = db.execute("SELECT id, title, slug FROM games ORDER BY title").fetchall()
    
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        game_id = request.form.get("game_id") or None

        if not title or not content:
            flash("タイトルと内容を入力してください。", "danger")
            return render_template("create_post.html", games=games)

        game_id = int(game_id) if game_id else None
        db.execute("INSERT INTO posts (user_id, game_id, title, content) VALUES (?, ?, ?, ?)",
                   (g.user_id, game_id, title, content))
        db.commit()
        flash("投稿完了！", "success")
        return redirect(url_for("index"))

    return render_template("create_post.html", games=games)

@app.route("/games/<game_slug>/<int:game_id>")
def game_detail(game_slug, game_id):
    db = get_db()
    game = db.execute("SELECT id, title, genre, developer FROM games WHERE id = ? AND slug = ?", 
                      (game_id, game_slug)).fetchone()
    
    if not game:
        flash("ゲームが見つかりません。", "danger")
        return redirect(url_for("index"))

    posts = db.execute("""
        SELECT p.id, p.title, p.content, p.created_at, u.username
        FROM posts p JOIN users u ON p.user_id = u.id
        WHERE p.game_id = ?
        ORDER BY p.created_at DESC
    """, (game_id,)).fetchall()
    
    formatted_posts = []
    for post in posts:
        post_dict = dict(post)
        dt_utc = datetime.strptime(post_dict['created_at'], '%Y-%m-%d %H:%M:%S')
        dt_jst = dt_utc.replace(tzinfo=timezone.utc).astimezone(JST)
        post_dict['created_at_fmt'] = dt_jst.strftime('%Y年%m月%d日 %H:%M')
        formatted_posts.append(post_dict)

    return render_template("game_detail.html", game=game, posts=formatted_posts)

if __name__ == "__main__":
    print("💡 開発環境: データベースを初期化中...")
    with app.app_context():
        init_db()
    print("✅ 初期化完了")
    app.run(debug=True, port=8000)