from flask import Flask, render_template, request, redirect, url_for, g, session
from dotenv import load_dotenv
import db_config
import bcrypt
from datetime import datetime
import os
from werkzeug.utils import secure_filename
from thefuzz import fuzz

# .envファイルをロード (ローカル開発用)
load_dotenv() 

app = Flask(__name__)
# Renderでは環境変数からSECRET_KEYを取得するのが一般的です
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key_here') # 環境変数がなければデフォルトを使用
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# uploadsフォルダが存在しない場合に作成
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ==========================================================
# 🚨 Renderデプロイ時にテーブルを作成するロジック
# Render環境変数 RUN_MIGRATIONS=True の時のみ実行
if os.environ.get('RUN_MIGRATIONS') == 'True' and os.environ.get('DATABASE_URL'):
    print("--- 💡 Running initial database setup (Migrations)... ---")
    try:
        db_config.create_tables()
        print("--- ✅ Database setup complete! Remember to remove RUN_MIGRATIONS=True from Render! ---")
    except Exception as e:
        print(f"--- ❌ Database setup failed: {e} ---")
# ==========================================================


# リクエストごとにデータベース接続を確立
def get_db():
    if 'db' not in g:
        try:
            # db_config.pyでPostgres接続が定義されている
            g.db = db_config.get_db_connection()
        except Exception as e:
            # データベース接続失敗時
            print(f"Database connection error: {e}")
            g.db = None
    return g.db

# リクエスト終了時にデータベース接続を閉じる
@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# トップページ
@app.route('/', methods=['GET', 'POST'])
def index():
    db = get_db()
    if not db:
        # DB接続失敗時はエラーメッセージを表示
        return render_template('index.html', error_message="データベース接続エラーが発生しました。", games=[], logged_in=False)
    cursor = db.cursor()

    logged_in = 'user_id' in session
    
    # 新しいゲームスレッドの投稿処理（現在は create_thread ルートに移動推奨ですが、ここではこのまま）
    if request.method == 'POST' and 'game_title' in request.form:
        if not logged_in:
            return redirect(url_for('index'))
        game_title = request.form['game_title']
        game_url = request.form.get('game_url', None)
        
        if game_title:
            try:
                # PostgreSQLでは RETURNING id で新しいIDを取得
                sql = "INSERT INTO games (title, user_id, game_url) VALUES (%s, %s, %s) RETURNING id"
                cursor.execute(sql, (game_title, session['user_id'], game_url))
                new_id = cursor.fetchone()['id']
                db.commit()
                return redirect(url_for('game_thread', game_id=new_id))
            except Exception as e:
                db.rollback()
                return f"Error creating thread: {e}"

    search_query = request.args.get('search', '')
    sort_by = request.args.get('sort', 'new')

    if sort_by == 'comments':
        # コメント数でソート
        sql_games = """
            SELECT games.id, games.title, games.user_id, games.game_url, games.created_at, COUNT(posts.id) AS post_count
            FROM games
            LEFT JOIN posts ON games.id = posts.game_id
            GROUP BY games.id
            ORDER BY post_count DESC, games.created_at DESC
        """
    else:
        # 新しい順にソート
        sql_games = "SELECT id, title, user_id, game_url, created_at FROM games ORDER BY created_at DESC"
        
    try:
        cursor.execute(sql_games)
        all_games = cursor.fetchall()
    except Exception as e:
        print(f"Error fetching games: {e}")
        all_games = []
        # ゲーム取得失敗時も、エラーメッセージ付きでページを表示
        return render_template('index.html', error_message=f"ゲーム一覧の取得中にエラーが発生しました: {e}", games=[], logged_in=logged_in, session=session, search_query=search_query, sort_by=sort_by)

    if search_query:
        search_results = []
        for game in all_games:
            # タイトルと検索クエリの部分一致度を計算
            if fuzz.partial_ratio(search_query.lower(), game['title'].lower()) >= 75:
                search_results.append(game)
        games = search_results
    else:
        games = all_games
    
    return render_template('index.html', games=games, logged_in=logged_in, session=session, search_query=search_query, sort_by=sort_by)

# 登録処理
@app.route('/register', methods=['POST'])
def register():
    db = get_db()
    cursor = db.cursor()

    username = request.form['username']
    password = request.form['password']

    try:
        # ユーザー名の重複チェック
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            return render_template('index.html', error_register="そのユーザー名は既に使われています。別の名前を選んでください。")

        # パスワードのハッシュ化
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # ユーザー登録
        sql = "INSERT INTO users (username, password_hash) VALUES (%s, %s)" # DBの列名 'password_hash' に修正
        cursor.execute(sql, (username, hashed_password))
        db.commit()
        return redirect(url_for('index'))
    except Exception as e:
        db.rollback()
        return f"Error registering user: {e}"

# ログイン処理
@app.route('/login', methods=['POST'])
def login():
    db = get_db()
    cursor = db.cursor()

    username = request.form['username']
    password = request.form['password']

    # ユーザー取得（PostgreSQLの列名に合わせて 'password_hash' を取得）
    sql = "SELECT id, password_hash, username FROM users WHERE username = %s"
    cursor.execute(sql, (username,))
    user = cursor.fetchone()
    
    # ユーザーが存在し、パスワードが一致する場合
    if user and bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
        session['user_id'] = user['id']
        session['username'] = user['username']
        return redirect(url_for('index'))
    else:
        # ログイン失敗時は再描画
        cursor.execute("SELECT id, title, user_id, game_url, created_at FROM games ORDER BY created_at DESC")
        games = cursor.fetchall()
        logged_in = 'user_id' in session
        return render_template('index.html', error_login="ユーザー名またはパスワードが間違っています。", games=games, logged_in=logged_in, session=session, search_query='', sort_by='new')


# ログアウト処理
@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    return redirect(url_for('index'))

# ゲームスレッド作成処理（POSTメソッドはindexからこちらへリダイレクトされる想定）
@app.route('/create_thread', methods=['POST'])
def create_thread():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    db = get_db()
    cursor = db.cursor()
    
    game_title = request.form['game_title']
    game_url = request.form.get('game_url', None)

    if game_title:
        try:
            # PostgreSQLのRETURNINGでIDを取得
            sql = "INSERT INTO games (title, user_id, game_url) VALUES (%s, %s, %s) RETURNING id" 
            cursor.execute(sql, (game_title, session['user_id'], game_url))
            new_id = cursor.fetchone()['id']
            db.commit()
            return redirect(url_for('game_thread', game_id=new_id))
        except Exception as e:
            db.rollback()
            return f"Error: {e}"
            
    return redirect(url_for('index'))

# スレッドページ
@app.route('/thread/<int:game_id>', methods=['GET', 'POST'])
def game_thread(game_id):
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        if 'user_id' not in session:
            return redirect(url_for('index'))
        
        content = request.form['content']
        media_file = request.files.get('media')
        media_url = None

        if media_file and media_file.filename != '':
            filename = secure_filename(media_file.filename)
            media_url = filename
            media_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        if content or media_url:
            try:
                sql = "INSERT INTO posts (game_id, content, user_id, media_url) VALUES (%s, %s, %s, %s)"
                cursor.execute(sql, (game_id, content, session['user_id'], media_url))
                db.commit()
                return redirect(url_for('game_thread', game_id=game_id))
            except Exception as e:
                db.rollback()
                return f"Error posting comment: {e}"

    # ゲームスレッドの基本情報を取得
    sql_game = "SELECT id, title, user_id, game_url FROM games WHERE id = %s"
    cursor.execute(sql_game, (game_id,))
    game = cursor.fetchone()
    if not game:
        return redirect(url_for('index'))
    
    post_sort_by = request.args.get('post_sort', 'new')

    # 投稿一覧の取得（「いいね」数、連番付き）
    if post_sort_by == 'likes':
        sql_posts = """
            SELECT 
                posts.id, posts.content, posts.created_at, posts.user_id, posts.media_url, 
                users.username, COUNT(likes.id) AS like_count,
                ROW_NUMBER() OVER (PARTITION BY posts.game_id ORDER BY posts.created_at) AS post_number
            FROM posts
            JOIN users ON posts.user_id = users.id
            LEFT JOIN likes ON posts.id = likes.post_id
            WHERE posts.game_id = %s
            GROUP BY posts.id, posts.content, posts.created_at, posts.user_id, posts.media_url, users.username
            ORDER BY like_count DESC, posts.created_at DESC
        """
    else:
        sql_posts = """
            SELECT 
                posts.id, posts.content, posts.created_at, posts.user_id, posts.media_url, 
                users.username, COUNT(likes.id) AS like_count,
                ROW_NUMBER() OVER (PARTITION BY posts.game_id ORDER BY posts.created_at) AS post_number
            FROM posts
            JOIN users ON posts.user_id = users.id
            LEFT JOIN likes ON posts.id = likes.post_id
            WHERE posts.game_id = %s
            GROUP BY posts.id, posts.content, posts.created_at, posts.user_id, posts.media_url, users.username
            ORDER BY posts.created_at ASC 
        """
    cursor.execute(sql_posts, (game_id,))
    posts = cursor.fetchall()

    # ログインユーザーが各投稿に「いいね」しているかチェック
    logged_in = 'user_id' in session
    if logged_in:
        user_id = session['user_id']
        for post in posts:
            cursor.execute("SELECT COUNT(*) FROM likes WHERE post_id = %s AND user_id = %s", (post['id'], user_id))
            # DictCursorを使用している場合でも、COUNT(*)の結果はタプルまたはリストで返される場合があるため、インデックスで取得
            # psycopg2.extras.DictCursorを使用しても、COUNTの結果は列名なしで返るため、インデックスで取得
            result = cursor.fetchone()
            # PostgreSQLのDictCursorを使うと、fetchone()はDictRowを返すため、インデックス0で取得
            post['is_liked'] = result[0] > 0 if isinstance(result, (list, tuple)) else result['count'] > 0

    
    return render_template('game_thread.html', game=game, posts=posts, logged_in=logged_in, session=session, post_sort_by=post_sort_by)

# 「いいね」処理
@app.route('/like_post/<int:post_id>', methods=['POST'])
def like_post(post_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    db = get_db()
    cursor = db.cursor()
    user_id = session['user_id']
    
    try:
        # 既存の「いいね」をチェック
        cursor.execute("SELECT id FROM likes WHERE post_id = %s AND user_id = %s", (post_id, user_id))
        existing_like = cursor.fetchone()
        
        if existing_like:
            # 既に「いいね」があれば削除（取り消し）
            cursor.execute("DELETE FROM likes WHERE post_id = %s AND user_id = %s", (post_id, user_id))
        else:
            # なければ追加
            cursor.execute("INSERT INTO likes (post_id, user_id) VALUES (%s, %s)", (post_id, user_id))
        db.commit()
    except Exception as e:
        db.rollback()
        return f"Error processing like: {e}"
        
    # 元のページに戻る
    referer = request.headers.get('Referer')
    if referer:
        return redirect(referer)
    return redirect(url_for('index'))

# 投稿削除ルート
@app.route('/delete_post/<int:post_id>', methods=['POST'])
def delete_post(post_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    db = get_db()
    cursor = db.cursor()

    sql_select = "SELECT user_id, media_url, game_id FROM posts WHERE id = %s"
    cursor.execute(sql_select, (post_id,))
    post = cursor.fetchone()

    if post and post['user_id'] == session['user_id']:
        try:
            # メディアファイルがあれば削除
            if post['media_url']:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], post['media_url'])
                if os.path.exists(filepath):
                    os.remove(filepath)
            
            # 投稿を削除（likesテーブルもカスケード削除されることを想定）
            sql_delete = "DELETE FROM posts WHERE id = %s"
            cursor.execute(sql_delete, (post_id,))
            db.commit()
            
            # 削除後、スレッドページへ戻る
            return redirect(url_for('game_thread', game_id=post['game_id']))
            
        except Exception as e:
            db.rollback()
            return f"Error deleting post: {e}"

    return redirect(url_for('index'))

# スレッド削除ルート
@app.route('/delete_thread/<int:game_id>', methods=['POST'])
def delete_thread(game_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))

    db = get_db()
    cursor = db.cursor()

    sql_select = "SELECT user_id FROM games WHERE id = %s"
    cursor.execute(sql_select, (game_id,))
    game = cursor.fetchone()

    if game and game['user_id'] == session['user_id']:
        try:
            # 投稿に紐づくメディアファイルを先に削除
            cursor.execute("SELECT media_url FROM posts WHERE game_id = %s", (game_id,))
            media_files = cursor.fetchall()
            for media in media_files:
                if media['media_url']:
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], media['media_url'])
                    if os.path.exists(filepath):
                        os.remove(filepath)

            # ゲームスレッドを削除（投稿、いいねもカスケード削除されることを想定）
            sql_delete = "DELETE FROM games WHERE id = %s"
            cursor.execute(sql_delete, (game_id,))
            db.commit()
        except Exception as e:
            db.rollback()
            return f"Error deleting thread: {e}"

    return redirect(url_for('index'))

# プロフィールページ
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    db = get_db()
    cursor = db.cursor()

    user_id = session['user_id']
    
    cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    
    # 自分で立てたスレッド
    cursor.execute("SELECT id, title, created_at FROM games WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
    my_threads = cursor.fetchall()
    
    # 自分の投稿
    sql_my_posts = """
        SELECT posts.id, posts.content, posts.created_at, posts.media_url, games.title AS game_title, games.id AS game_id
        FROM posts
        JOIN games ON posts.game_id = games.id
        WHERE posts.user_id = %s
        ORDER BY posts.created_at DESC
    """
    cursor.execute(sql_my_posts, (user_id,))
    my_posts = cursor.fetchall()
    
    return render_template('profile.html', user=user, my_threads=my_threads, my_posts=my_posts)


if __name__ == '__main__':
    # ローカル開発時にテーブルがなければ作成する
    if os.environ.get('DATABASE_URL'):
        db_config.create_tables() 
    app.run(debug=True)
