import os
import psycopg2
from urllib.parse import urlparse

# DATABASE_URL環境変数から接続情報を取得する関数
def get_db_connection():
    # Render環境ではDATABASE_URLが設定されているはず
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        # ローカル開発環境の場合の処理（通常は.envから取得）
        raise ValueError("DATABASE_URL環境変数が設定されていません。")
    
    # psycopg2で接続するためにURLをパース
    url = urlparse(database_url)
    conn = psycopg2.connect(
        database=url.path[1:],
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port,
        sslmode='require' # RenderのPostgreSQLはSSL接続が必要
    )
    return conn

# 🚨 この関数がテーブルを作成します
def create_tables():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # ⚠️ ここに、アプリケーションで使う全てのテーブル作成SQLを記述します
        # 'games'テーブルが存在しない場合に作成
        cur.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                user_id INTEGER NOT NULL,
                game_url TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # ⚠️ 例: 'users'テーブルもここで作成
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(80) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            );
        """)
        
        conn.commit()
        print("Database tables created successfully!")
    except Exception as e:
        print(f"Database creation error: {e}")
    finally:
        if conn:
            conn.close()

# 既存のdb_config.pyの他の関数はそのまま残してください