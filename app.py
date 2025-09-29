import os
import secrets
import functools
import time
import psycopg2
from psycopg2 import extras
from flask import Flask, render_template, request, redirect, url_for, session, g
from werkzeug.utils import secure_filename
from flask_session import Session # セッション永続化
import bcrypt # パスワードハッシュ化
from dotenv import load_dotenv # 環境変数ロード
# db_configから必要な関数をインポート
from db_config import get_db_connection, create_tables, get_db_url 

# ------------------------------
# 1. 初期設定とアプリケーション設定
# ------------------------------
load_dotenv() # .envファイルから環境変数をロード (ローカル開発用)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(16)) 
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4'}

# ------------------------------
# 1.5. Flask-Session設定 (セッション永続化の鍵)
# ------------------------------

try:
    db_url = get_db_url() # db_configからSQLAlchemy形式のURLを取得
except ValueError as e:
    print(f"Warning: {e}. Using local fallback URI.")
    # 環境変数が設定されていない場合のフォールバック
    db_url = "postgresql://user:password@localhost/defaultdb" 

# 💡 Flask-SessionがDBに接続するための設定（セッション維持の最終手段）
app.config["SESSION_TYPE"] = "sqlalchemy"
app.config["SESSION_SQLALCHEMY_TABLE"] = "sessions"
app.config["SESSION_PERMANENT"] = True
app.config["SESSION_USE_SIGNER"] = True 

# 🚨 最終修正: Flask-Sessionに対して、使用するDB URIを明示的に指定します
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SESSION_SQLALCHEMY_DATABASE_URI"] = db_url 

# 💡 Render/HTTPS環境に対応したクッキー設定（セッション維持の鍵） 💡
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PREFERRED_URL_SCHEME'] = 'https' 

# Flask-Sessionの初期化
sess = Session(app) 

# ------------------------------
# 2. データベース接続管理 (psycopg2を使用)
# ------------------------------

def get_db():
    """リクエストごとにデータベース接続を取得・管理する"""
    if 'db' not in g:
        try:
            g.db = get_db_connection()
        except Exception as e:
            app.logger.error(f"Failed to connect to database: {e}")
            raise RuntimeError("Database connection failed.") from e
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    """リクエスト終了後にデータベース接続を閉じる"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

# ------------------------------
# 3. ユーティリティ関数 (bcrypt使用)
# ------------------------------

def hash_password(password):
    """パスワードをbcryptでハッシュ化する"""
    salt = bcrypt.gensalt()
    # 文字列をバイトに変換してからハッシュ化
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt) 
    return hashed.decode('utf-8') 

def check_password(hashed_password, password):
    """bcryptハッシュと入力されたパスワードを比較する"""
    try:
        # ハッシュ化されたパスワードと入力されたパスワードの両方をバイトに変換
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except ValueError:
        return False

def allowed_file(filename):
    """許可された拡張子のファイルかどうかをチェック"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(view):
    """ログインを要求するデコレータ"""
    @functools.wraps(view)
    def wrapped_view(*args, **kwargs):
        if 'user_id' not in session: 
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped_view

# ------------------------------
# 4. データベースマイグレーション
# ------------------------------

# Render環境変数 RUN_MIGRATIONS=True が設定されている場合のみテーブル作成を実行
if os.environ.get('RUN_MIGRATIONS') == 'True':
    print("--- 💡 Running initial database setup (Migrations)... ---")
    try:
        conn = get_db_connection()
        create_tables(conn)
        conn.close()
        print("--- ✅ Database setup complete! Remember to remove RUN_MIGRATIONS=True from Render! ---")
    except Exception as e:
        print(f"--- ❌ Database setup failed: {e} ---")

# ------------------------------
# 5. ルーティングとDB操作 
# ------------------------------

@app.route('/')
def index():
    db = get_db()
    cursor = db.cursor(cursor_factory=extras.DictCursor)
    
    # ユーザー名を表示するためにJOIN
    sql = """
    SELECT 
        g.id, g.title, g.url as game_url, g.created_at, u.username 
    FROM games g 
    JOIN users u ON g.user_id = u.id 
    ORDER BY g.created_at DESC;
    """
    
    try:
        cursor.execute(sql)
        games = cursor.fetchall()
    except psycopg2.errors.UndefinedTable:
        # テーブルがない場合は空リストを返し、クラッシュを防ぐ
        games = []
        app.logger.warning("Warning: 'games' table does not exist. Returning empty list.")
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error fetching games: {e}")
        games = []

    return render_template('index.html', games=games)

# --- ログイン・ログアウト ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cursor = db.cursor(cursor_factory=extras.DictCursor) 
        
        sql = "SELECT id, password_hash, username FROM users WHERE username = %s;"
        try:
            cursor.execute(sql, (username,))
            user = cursor.fetchone()
        except Exception as e:
            db.rollback()
            return render_template('login.html', error=f"データベースエラー: {e}")

        # ユーザーが存在し、パスワードが一致した場合
        if user and check_password(user['password_hash'], password):
            # ログイン成功
            session['user_id'] = user['id']
            session['username'] = user['username']
            # print(f"DEBUG: Login successful. Session user_id set to {user['id']}") # デバッグ用
            return redirect(url_for('index')) 
        else:
            return render_template('login.html', error='ユーザー名またはパスワードが違います')
    
    # GETリクエストの場合
    return render_template('login.html', is_register=False)

# --- 登録処理 ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if len(username) < 3 or len(password) < 6:
             return render_template('login.html', error='ユーザー名は3文字以上、パスワードは6文字以上が必要です', is_register=True)

        hashed_password = hash_password(password)
        db = get_db()
        cursor = db.cursor()
        
        sql = "INSERT INTO users (username, password_hash) VALUES (%s, %s);"
        try:
            cursor.execute(sql, (username, hashed_password))
            db.commit()
            # 登録成功後はログイン画面へリダイレクト
            return redirect(url_for('login')) 
        except psycopg2.errors.UniqueViolation:
            db.rollback()
            return render_template('login.html', error='そのユーザー名は既に使用されています', is_register=True)
        except Exception as e:
            db.rollback()
            return render_template('login.html', error=f"データベースエラー: {e}", is_register=True)
            
    # GETリクエストの場合
    return render_template('login.html', is_register=True)

@app.route('/logout')
def logout():
    # セッションから情報を削除
    session.clear() 
    return redirect(url_for('index'))

# --- スレッド作成 ---

@app.route('/create_game', methods=['GET', 'POST'])
@login_required
def create_game():
    if request.method == 'POST':
        title = request.form['title']
        game_url = request.form.get('game_url', '') 
        user_id = session['user_id']
        
        if not title:
            return render_template('create_game.html', error="タイトルは必須です")

        db = get_db()
        cursor = db.cursor(cursor_factory=extras.DictCursor)
        
        # game_url のカラム名が 'url' である可能性があるため、SQLを修正
        sql = "INSERT INTO games (title, user_id, url) VALUES (%s, %s, %s) RETURNING id;"
        
        try:
            cursor.execute(sql, (title, user_id, game_url))
            new_game_id = cursor.fetchone()['id']
            db.commit()
            return redirect(url_for('game_thread', game_id=new_game_id))
        except Exception as e:
            db.rollback()
            return render_template('create_game.html', error=f"データベースエラー: {e}")

    return render_template('create_game.html')

# --- スレッド詳細とコメント投稿 ---

@app.route('/thread/<int:game_id>', methods=['GET', 'POST'])
def game_thread(game_id):
    # game_thread 関数内で get_db() を呼び出して g.db が存在することを保証
    db = get_db()
    cursor = db.cursor(cursor_factory=extras.DictCursor) 
    
    if request.method == 'POST':
        if 'user_id' not in session:
            return redirect(url_for('login'))

        content = request.form.get('content', '').strip()
        media_file = request.files.get('media_file')
        user_id = session['user_id']
        media_filename = None

        if media_file and media_file.filename != '' and allowed_file(media_file.filename):
            filename = secure_filename(media_file.filename)
            media_filename = f"{int(time.time())}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], media_filename)
            media_file.save(filepath)
            
        if not content and not media_filename:
            return redirect(url_for('game_thread', game_id=game_id)) 

        try:
            sql = "INSERT INTO posts (game_id, user_id, content, media_url) VALUES (%s, %s, %s, %s) RETURNING id;"
            cursor.execute(sql, (game_id, user_id, content, media_filename))
            cursor.fetchone() 
            db.commit()
            return redirect(url_for('game_thread', game_id=game_id))
        
        except Exception as e:
            db.rollback()
            app.logger.error(f"Post error on game {game_id}: {e}")
            return f"コメント投稿時のデータベースエラーが発生しました: {e}"

    # GET リクエスト (スレッド表示)
    
    game_sql = """
    SELECT 
        g.id, g.title, g.url as game_url, g.created_at, u.username, u.id as creator_id
    FROM games g 
    JOIN users u ON g.user_id = u.id 
    WHERE g.id = %s;
    """
    cursor.execute(game_sql, (game_id,))
    game = cursor.fetchone()
    
    if not game:
        return "ゲームスレッドが見つかりません", 404

    posts_sql = """
    SELECT 
        p.id, p.content, p.media_url, p.created_at, u.username, 
        COUNT(l.id) AS like_count,
        EXISTS(SELECT 1 FROM likes WHERE post_id = p.id AND user_id = %s) AS user_liked
    FROM posts p 
    JOIN users u ON p.user_id = u.id
    LEFT JOIN likes l ON p.id = l.post_id
    WHERE p.game_id = %s
    GROUP BY p.id, u.username, p.content, p.media_url, p.created_at
    ORDER BY p.created_at ASC;
    """
    
    current_user_id = session.get('user_id', -1) 
    cursor.execute(posts_sql, (current_user_id, game_id))
    posts = cursor.fetchall()
    
    return render_template('game_thread.html', game=game, posts=posts, user_id=current_user_id)

# --- いいね処理 ---

@app.route('/like/<int:post_id>', methods=['POST'])
@login_required
def like_post(post_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    check_sql = "SELECT id FROM likes WHERE post_id = %s AND user_id = %s;"
    cursor.execute(check_sql, (post_id, user_id))
    existing_like = cursor.fetchone()

    try:
        if existing_like:
            # 既に「いいね」がある場合は削除 (いいね解除)
            delete_sql = "DELETE FROM likes WHERE post_id = %s AND user_id = %s;"
            cursor.execute(delete_sql, (post_id, user_id))
        else:
            # 「いいね」がない場合は追加
            insert_sql = "INSERT INTO likes (post_id, user_id) VALUES (%s, %s) RETURNING id;"
            cursor.execute(insert_sql, (post_id, user_id))
            cursor.fetchone() 
        
        db.commit()

    except Exception as e:
        db.rollback()
        app.logger.error(f"Like/Unlike Error: {e}")
        # エラーが発生した場合は元のページに戻す
        return redirect(request.referrer or url_for('index'))

    return redirect(request.referrer or url_for('index'))


# --- スレッド削除 ---
@app.route('/delete_thread/<int:game_id>', methods=['POST'])
@login_required
def delete_thread(game_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(cursor_factory=extras.DictCursor)
    
    try:
        # スレッドの作成者であることを確認
        cursor.execute("SELECT user_id FROM games WHERE id = %s;", (game_id,))
        game = cursor.fetchone()

        if game and game['user_id'] == user_id:
            # 関連するいいね、コメント、そしてスレッド本体を削除
            cursor.execute("DELETE FROM likes WHERE post_id IN (SELECT id FROM posts WHERE game_id = %s);", (game_id,))
            cursor.execute("DELETE FROM posts WHERE game_id = %s;", (game_id,))
            cursor.execute("DELETE FROM games WHERE id = %s;", (game_id,))
            db.commit()
        else:
            # 作成者ではない場合は何もしない
            pass 
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error deleting thread {game_id}: {e}")
    
    return redirect(url_for('index'))


if __name__ == '__main__':
    # ローカル実行時に uploads フォルダが存在しない場合は作成
    if not os.path.exists('static/uploads'):
        os.makedirs('static/uploads')
    # デバッグモードでアプリケーションを起動
    app.run(debug=True)
