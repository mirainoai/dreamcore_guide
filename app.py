from flask import Flask, render_template, request, redirect, url_for, g, session
import db_config
import bcrypt
from datetime import datetime
import os
from werkzeug.utils import secure_filename
from thefuzz import fuzz
# PostgreSQLに移行したため、PyMySQLの代わりにpsycopg2（db_config内で使用）を使います
# import pymysql # <-- 削除

app = Flask(__name__)
# Renderでは環境変数からSECRET_KEYを取得するのが一般的ですが、今回はそのまま
app.secret_key = 'your_secret_key_here' 
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# uploadsフォルダが存在しない場合に作成（Renderのビルド時に実行されます）
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


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
        return "データベース接続エラー", 500
    cursor = db.cursor()

    logged_in = 'user_id' in session
    
    if request.method == 'POST' and 'game_title' in request.form:
        if not logged_in:
            return redirect(url_for('index'))
        game_title = request.form['game_title']
        game_url = request.form.get('game_url', None)
        
        if game_title:
            try:
                sql = "INSERT INTO games (title, user_id, game_url) VALUES (%s, %s, %s)"
                cursor.execute(sql, (game_title, session['user_id'], game_url))
                db.commit()
                # Postgreではcursor.lastrowidが使えないため、最新のIDを取得するクエリが必要ですが、
                # App Engineで使っていたMySQLのcursor.lastrowidに近い挙動をするようdb_config.pyを修正したため
                # ここではそのまま lastrowid を使います。
                return redirect(url_for('game_thread', game_id=cursor.lastrowid))
            except Exception as e:
                db.rollback()
                return f"Error: {e}"

    search_query = request.args.get('search', '')
    sort_by = request.args.get('sort', 'new')

    if sort_by == 'comments':
        sql_games = """
            SELECT games.id, games.title, games.user_id, games.game_url, COUNT(posts.id) AS post_count
            FROM games
            LEFT JOIN posts ON games.id = posts.game_id
            GROUP BY games.id, games.title, games.user_id, games.game_url, games.created_at
            ORDER BY post_count DESC, games.created_at DESC
        """
    else:
        sql_games = "SELECT id, title, user_id, game_url FROM games ORDER BY created_at DESC"
        
    try:
        cursor.execute(sql_games)
        all_games = cursor.fetchall()
    except Exception as e:
        print(f"Error fetching games: {e}")
        all_games = []

    if search_query:
        search_results = []
        for game in all_games:
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
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            return render_template('index.html', error_register="そのユーザー名は既に使われています。別の名前を選んでください。")

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        sql = "INSERT INTO users (username, password) VALUES (%s, %s)"
        cursor.execute(sql, (username, hashed_password))
        db.commit()
        return redirect(url_for('index'))
    except Exception as e:
        db.rollback()
        return f"Error: {e}"

# ログイン処理
@app.route('/login', methods=['POST'])
def login():
    db = get_db()
    cursor = db.cursor()

    username = request.form['username']
    password = request.form['password']

    sql = "SELECT id, password, username FROM users WHERE username = %s"
    cursor.execute(sql, (username,))
    user = cursor.fetchone()
    
    # ユーザーが存在し、パスワードが一致する場合
    if user and bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
        session['user_id'] = user['id']
        session['username'] = user['username']
        return redirect(url_for('index'))
    else:
        # ログイン失敗時は再描画
        cursor.execute("SELECT id, title, user_id, game_url FROM games ORDER BY created_at DESC")
        games = cursor.fetchall()
        logged_in = 'user_id' in session
        return render_template('index.html', error_login="ユーザー名またはパスワードが間違っています。", games=games, logged_in=logged_in, session=session, search_query='', sort_by='new')


# ログアウト処理
@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    return redirect(url_for('index'))

# ゲームスレッド作成処理
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
            sql = "INSERT INTO games (title, user_id, game_url) VALUES (%s, %s, %s) RETURNING id" # PostgreSQLではRETURNINGでIDを取得
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
                return f"Error: {e}"

    sql_game = "SELECT id, title, user_id, game_url FROM games WHERE id = %s"
    cursor.execute(sql_game, (game_id,))
    game = cursor.fetchone()
    if not game:
        return redirect(url_for('index'))
    
    post_sort_by = request.args.get('post_sort', 'new')

    # PostgreSQLのウィンドウ関数に対応したSQLに修正 (PostgreSQLではサブクエリで連番を振るのが一般的)
    # MySQLの 'COUNT(*) FROM posts AS p WHERE p.game_id = posts.game_id AND p.created_at <= posts.created_at' はPostgresではROW_NUMBER()で実現できる
    if post_sort_by == 'likes':
        sql_posts = """
            SELECT posts.id, posts.content, posts.created_at, posts.user_id, posts.media_url, users.username, COUNT(likes.id) AS like_count,
            ROW_NUMBER() OVER (PARTITION BY posts.game_id ORDER BY posts.created_at) AS post_number
            FROM posts
            JOIN users ON posts.user_id = users.id
            LEFT JOIN likes ON posts.id = likes.post_id
            WHERE posts.game_id = %s
            GROUP BY posts.id, users.username
            ORDER BY like_count DESC, posts.created_at DESC
        """
    else:
        sql_posts = """
            SELECT posts.id, posts.content, posts.created_at, posts.user_id, posts.media_url, users.username, COUNT(likes.id) AS like_count,
            ROW_NUMBER() OVER (PARTITION BY posts.game_id ORDER BY posts.created_at) AS post_number
            FROM posts
            JOIN users ON posts.user_id = users.id
            LEFT JOIN likes ON posts.id = likes.post_id
            WHERE posts.game_id = %s
            GROUP BY posts.id, users.username
            ORDER BY posts.created_at DESC
        """
    cursor.execute(sql_posts, (game_id,))
    posts = cursor.fetchall()

    if 'user_id' in session:
        user_id = session['user_id']
        for post in posts:
            cursor.execute("SELECT COUNT(*) FROM likes WHERE post_id = %s AND user_id = %s", (post['id'], user_id))
            # psycopg2.extras.DictCursorを使用しても、COUNTの結果は列名なしで返るため、インデックスで取得
            post['is_liked'] = cursor.fetchone()[0] > 0
    else:
        for post in posts:
            post['is_liked'] = False

    logged_in = 'user_id' in session
    
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
        cursor.execute("SELECT id FROM likes WHERE post_id = %s AND user_id = %s", (post_id, user_id))
        existing_like = cursor.fetchone()
        
        if existing_like:
            cursor.execute("DELETE FROM likes WHERE id = %s", (existing_like['id'],))
        else:
            cursor.execute("INSERT INTO likes (post_id, user_id) VALUES (%s, %s)", (post_id, user_id))
        db.commit()
    except Exception as e:
        db.rollback()
        return f"Error: {e}"
        
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

    sql_select = "SELECT user_id, media_url FROM posts WHERE id = %s"
    cursor.execute(sql_select, (post_id,))
    post = cursor.fetchone()

    if post and post['user_id'] == session['user_id']:
        try:
            if post['media_url']:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], post['media_url'])
                if os.path.exists(filepath):
                    os.remove(filepath)
            
            sql_delete = "DELETE FROM posts WHERE id = %s"
            cursor.execute(sql_delete, (post_id,))
            db.commit()
        except Exception as e:
            db.rollback()
            return f"Error: {e}"

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
            cursor.execute("SELECT media_url FROM posts WHERE game_id = %s", (game_id,))
            media_files = cursor.fetchall()
            for media in media_files:
                if media['media_url']:
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], media['media_url'])
                    if os.path.exists(filepath):
                        os.remove(filepath)

            sql_delete = "DELETE FROM games WHERE id = %s"
            cursor.execute(sql_delete, (game_id,))
            db.commit()
        except Exception as e:
            db.rollback()
            return f"Error: {e}"

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
    
    cursor.execute("SELECT id, title, created_at FROM games WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
    my_threads = cursor.fetchall()
    
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

# if __name__ == '__main__': # <-- ローカル起動コードはRenderでは不要なため削除しました
#     app.run(debug=True)
# ローカル動作確認のために一時的に追加
