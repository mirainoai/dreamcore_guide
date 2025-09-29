import os
import secrets
import hashlib
import functools
import time
from flask import Flask, render_template, request, redirect, url_for, session, g
from werkzeug.utils import secure_filename
from db_config import get_db_connection, create_tables # db_configから関数をインポート
import psycopg2

# ------------------------------
# 1. アプリケーション設定
# ------------------------------

app = Flask(__name__)
# 環境変数からSECRET_KEYを取得。設定されていない場合は安全な乱数を生成。
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(16)) 
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MBまでのファイルを許可
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4'}

# ------------------------------
# 2. データベース接続管理
# ------------------------------

def get_db():
    """リクエストごとにデータベース接続を取得・管理する"""
    # gはFlaskのリクエスト固有のストレージ
    if 'db' not in g:
        try:
            # db_config.pyの関数を使って接続
            g.db = get_db_connection()
        except Exception as e:
            # 接続失敗は致命的
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
# 3. ユーティリティ関数
# ------------------------------

def hash_password(password):
    """パスワードをソルト付きでハッシュ化する"""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return f"{salt}${hashed}"

def check_password(hashed_password, password):
    """ハッシュ化されたパスワードと入力されたパスワードを比較する"""
    if not hashed_password or "$" not in hashed_password:
        return False
    salt, hashed = hashed_password.split('$', 1)
    return hashed == hashlib.sha256((password + salt).encode('utf-8')).hexdigest()

def allowed_file(filename):
    """許可された拡張子のファイルかどうかをチェック"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(view):
    """ログインを要求するデコレータ"""
    @functools.wraps(view)
    def wrapped_view(*args, **kwargs):
        if 'logged_in' not in session or not session['logged_in']:
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped_view

# ------------------------------
# 4. データベースマイグレーション
# ------------------------------

# Renderでの初回デプロイ時のみテーブルを作成するための処理
# 環境変数RUN_MIGRATIONSが'True'の場合にcreate_tablesを実行する
if os.environ.get('RUN_MIGRATIONS') == 'True':
    # この処理はgunicorn起動時に一度だけ実行される
    print("--- 💡 Running initial database setup (Migrations)... ---")
    try:
        create_tables()
        print("--- ✅ Database setup complete! Remember to remove RUN_MIGRATIONS=True from Render! ---")
    except Exception as e:
        print(f"--- ❌ Database setup failed: {e} ---")

# ------------------------------
# 5. ルーティングとDB操作 (PostgreSQL対応済み)
# ------------------------------

@app.route('/')
def index():
    db = get_db()
    cursor = db.cursor()
    
    # ユーザー名とゲーム情報を結合して取得
    sql = """
    SELECT 
        g.id, g.title, g.game_url, g.created_at, u.username 
    FROM games g 
    JOIN users u ON g.user_id = u.id 
    ORDER BY g.created_at DESC;
    """
    
    try:
        cursor.execute(sql)
        games = cursor.fetchall()
    except psycopg2.errors.UndefinedTable:
        # 'games'テーブルがまだ存在しない場合の例外処理（初回デプロイ時など）
        games = []
        app.logger.warning("Warning: 'games' table does not exist. Returning empty list.")
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error fetching games: {e}")
        games = []

    # テンプレートにデータを渡してレンダリング
    return render_template('index.html', games=games)

# --- ログイン・登録・ログアウト ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cursor = db.cursor()
        
        # ユーザー情報を取得
        sql = "SELECT id, password_hash, username FROM users WHERE username = %s;"
        try:
            cursor.execute(sql, (username,))
            user = cursor.fetchone()
        except Exception as e:
            db.rollback()
            return render_template('login.html', error=f"データベースエラー: {e}")

        # パスワードチェック
        if user and check_password(user['password_hash'], password):
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='ユーザー名またはパスワードが違います')
    
    return render_template('login.html')

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
        
        # ユーザー情報をDBに挿入
        sql = "INSERT INTO users (username, password_hash) VALUES (%s, %s);"
        try:
            cursor.execute(sql, (username, hashed_password))
            db.commit()
            return redirect(url_for('login'))
        except psycopg2.errors.UniqueViolation:
            # ユーザー名重複エラー
            db.rollback()
            return render_template('login.html', error='そのユーザー名は既に使用されています', is_register=True)
        except Exception as e:
            db.rollback()
            return render_template('login.html', error=f"データベースエラー: {e}", is_register=True)
            
    return render_template('login.html', is_register=True)

@app.route('/logout')
def logout():
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
        cursor = db.cursor()
        
        # 🚨 PostgreSQL対応: RETURNING id で新しいIDを取得
        sql = "INSERT INTO games (title, user_id, game_url) VALUES (%s, %s, %s) RETURNING id;"
        
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

@app.route('/game/<int:game_id>', methods=['GET', 'POST'])
def game_thread(game_id):
    db = get_db()
    cursor = db.cursor()
    
    # 投稿処理 (Internal Server Errorの原因箇所)
    if request.method == 'POST':
        # ログインチェック
        if 'logged_in' not in session or not session['logged_in']:
            return redirect(url_for('login'))

        content = request.form.get('content', '').strip()
        media_file = request.files.get('media_file')
        user_id = session['user_id']
        media_filename = None

        # 1. ファイルアップロード処理
        if media_file and media_file.filename != '' and allowed_file(media_file.filename):
            # ファイル名を安全に処理し、保存
            filename = secure_filename(media_file.filename)
            # タイムスタンプを付加してファイル名の衝突を防ぐ
            media_filename = f"{int(time.time())}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], media_filename)
            media_file.save(filepath)
            
        if not content and not media_filename:
            # 内容もファイルもない場合はエラー
            return redirect(url_for('game_thread', game_id=game_id)) # エラーメッセージなしでリダイレクト

        # 2. データベース挿入
        try:
            # 🚨 修正箇所: RETURNING id を使用
            sql = "INSERT INTO posts (game_id, user_id, content, media_url) VALUES (%s, %s, %s, %s) RETURNING id;"
            cursor.execute(sql, (game_id, user_id, content, media_filename))
            
            # 🚨 **Internal Server Error解消の鍵**: 
            # PostgreSQLでRETURNINGを使用したら、DBをコミットする前に必ずfetchone()でカーソルをクリアする必要があります。
            cursor.fetchone() 
            
            db.commit()
            return redirect(url_for('game_thread', game_id=game_id))
        
        except Exception as e:
            # データベースエラーの場合はロールバック
            db.rollback()
            # 開発中はエラーを表示してデバッグを容易にする
            app.logger.error(f"Post error on game {game_id}: {e}")
            # 本番環境ではInternal Server Errorを表示
            return f"コメント投稿時のデータベースエラーが発生しました: {e}"

    # GET リクエスト (スレッド表示)
    
    # 1. ゲーム情報を取得
    game_sql = """
    SELECT 
        g.id, g.title, g.game_url, g.created_at, u.username, u.id as creator_id
    FROM games g 
    JOIN users u ON g.user_id = u.id 
    WHERE g.id = %s;
    """
    cursor.execute(game_sql, (game_id,))
    game = cursor.fetchone()
    
    if not game:
        return "ゲームスレッドが見つかりません", 404

    # 2. コメント投稿を取得（ユーザー名と結合）
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
    
    # ログインしていればユーザーIDを渡し、いいね状況を取得
    current_user_id = session.get('user_id', -1) 
    cursor.execute(posts_sql, (current_user_id, game_id))
    posts = cursor.fetchall()
    
    # テンプレートをレンダリング
    return render_template('game_thread.html', game=game, posts=posts, user_id=current_user_id)

# --- いいね処理 ---

@app.route('/like/<int:post_id>', methods=['POST'])
@login_required
def like_post(post_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    # 1. 既に「いいね」しているかチェック
    check_sql = "SELECT id FROM likes WHERE post_id = %s AND user_id = %s;"
    cursor.execute(check_sql, (post_id, user_id))
    existing_like = cursor.fetchone()

    try:
        if existing_like:
            # 既に「いいね」済みなら、削除 (いいねの取り消し)
            delete_sql = "DELETE FROM likes WHERE post_id = %s AND user_id = %s;"
            cursor.execute(delete_sql, (post_id, user_id))
        else:
            # 未「いいね」なら、挿入
            # 🚨 PostgreSQL対応: RETURNING id を使用
            insert_sql = "INSERT INTO likes (post_id, user_id) VALUES (%s, %s) RETURNING id;"
            cursor.execute(insert_sql, (post_id, user_id))
            # 🚨 カーソルをクリア
            cursor.fetchone() 
        
        db.commit()

    except Exception as e:
        db.rollback()
        app.logger.error(f"Like/Unlike Error: {e}")
        # エラーメッセージを返す代わりに、元のスレッドにリダイレクト
        return redirect(request.referrer or url_for('index'))

    # 元のページに戻る
    return redirect(request.referrer or url_for('index'))


if __name__ == '__main__':
    # 開発環境でuploadsフォルダが存在しない場合は作成
    if not os.path.exists('static/uploads'):
        os.makedirs('static/uploads')
    app.run(debug=True)
