from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from dotenv import load_dotenv
import os
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# アプリ初期化
app = Flask(__name__)
load_dotenv()  # .envから環境変数を読み込み
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "default_fallback_key")  # セッション用
app.config["SESSION_COOKIE_DOMAIN"] = os.environ.get("SESSION_COOKIE_DOMAIN", "localhost")
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 3600  # 1時間持続
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # セッションセキュリティ

# 環境変数からDBパス取得
DATABASE_PATH = os.environ.get("DATABASE_PATH", "dreamcore_guide.db")

# データベース接続
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        g.db.row_factory = sqlite3.Row  # 辞書形式
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hash TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            game_url TEXT,
            game_title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    # 初期データ（オプション）
    db.execute("""
        INSERT OR IGNORE INTO posts (user_id, title, content, game_url, game_title)
        VALUES (?, ?, ?, ?, ?)
    """, (1, "初期投稿", "これはテスト投稿です。", "https://example.com", "テストゲーム"))
    db.commit()
    print("--- 💡 Running initial database setup (Migrations)... ---")
    print("--- ✅ Database setup complete! ---")

app.teardown_appcontext(close_db)

# ルート
@app.route("/")
def index():
    db = get_db()
    posts = db.execute("SELECT posts.*, users.username FROM posts JOIN users ON posts.user_id = users.id ORDER BY created_at DESC").fetchall()
    formatted_posts = [
        {
            "id": post["id"],
            "title": post["title"],
            "content": post["content"],
            "username": post["username"],
            "created_at_fmt": datetime.strptime(post["created_at"], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M") if isinstance(post["created_at"], str) else post["created_at"].strftime("%Y-%m-%d %H:%M"),
            "game_url": post["game_url"],
            "game_title": post["game_title"]
        } for post in posts
    ]
    print(f"Rendering index with {len(formatted_posts)} posts")  # デバッグ
    return render_template("index.html", posts=formatted_posts)

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        print(f"Session user_id: {session['user_id']}")  # デバッグ
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
                session.permanent = True  # 永続セッション
                print(f"Logged in user_id: {user['id']} - Permanent session set")  # ログイン成功ログ
                flash("ログイン成功！", "success")
                return redirect(url_for("index"))
            flash("ユーザー名またはパスワードが間違っています。", "danger")
        except sqlite3.Error as e:
            flash(f"エラー: {str(e)}", "danger")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if not username or not password:
            flash("ユーザー名とパスワードを入力してください。", "danger")
            return render_template("register.html")
        try:
            db = get_db()
            db.execute("INSERT INTO users (username, hash) VALUES (?, ?)", 
                      (username, generate_password_hash(password)))
            db.commit()
            flash("登録成功！ログインしてください。", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("そのユーザー名はすでに使用されています。", "danger")
        except sqlite3.Error as e:
            flash(f"エラー: {str(e)}", "danger")
    return render_template("register.html")

@app.route("/create", methods=["GET", "POST"])
def create_post():
    if not session.get("user_id"):
        flash("ログインしてください。", "danger")
        return redirect(url_for("login"))
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        game_url = request.form.get("game_url")
        game_title = request.form.get("game_title")
        if not title or not content:
            flash("タイトルと内容は必須です。", "danger")
            return render_template("create_post.html")
        try:
            db = get_db()
            db.execute("INSERT INTO posts (user_id, title, content, game_url, game_title) VALUES (?, ?, ?, ?, ?)",
                      (session["user_id"], title, content, game_url, game_title))
            db.commit()
            flash("投稿が作成されました！", "success")
            return redirect(url_for("index"))
        except sqlite3.Error as e:
            flash(f"エラー: {str(e)}", "danger")
    return render_template("create_post.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("ログアウトしました。", "success")
    return redirect(url_for("index"))

# デプロイ時初期化
if os.environ.get("RUN_MIGRATIONS", "False").lower() == "true":
    with app.app_context():
        init_db()

# ローカル実行
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True, port=8000)