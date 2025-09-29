import os
import psycopg2
from psycopg2 import extras
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SQLAlchemySession

# ------------------------------
# 1. データベース接続情報 (Render/環境変数)
# ------------------------------

def get_db_url():
    """環境変数からPostgreSQLの接続URLを取得する"""
    # Renderは 'EXTERNAL_DATABASE_URL' または 'DATABASE_URL' を提供
    db_url = os.environ.get('DATABASE_URL')
    if db_url is None:
        raise ValueError("DATABASE_URL environment variable is not set.")
        
    # psycopg2形式 (postgres://) を SQLAlchemy形式 (postgresql://) に変換
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql://", 1)
    return db_url

def get_engine():
    """SQLAlchemyエンジンを取得する (Flask-Session用)"""
    return create_engine(get_db_url())

def get_db_connection():
    """psycopg2接続オブジェクトを取得する (直接DB操作用)"""
    # DB URLを直接psycopg2に渡す
    conn = psycopg2.connect(get_db_url(), cursor_factory=extras.DictCursor)
    # Autocommitは無効に設定し、明示的にcommit/rollbackする
    conn.autocommit = False 
    return conn

# ------------------------------
# 2. テーブル作成関数
# ------------------------------

def create_users_table(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(80) UNIQUE NOT NULL,
        password_hash VARCHAR(100) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

def create_games_table(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS games (
        id SERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        game_url VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

def create_posts_table(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS posts (
        id SERIAL PRIMARY KEY,
        game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        content TEXT NOT NULL,
        media_url VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

def create_likes_table(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS likes (
        id SERIAL PRIMARY KEY,
        post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (post_id, user_id)
    );
    """)
    
def create_session_table(cur):
    """Flask-Sessionが使用するsessionsテーブルを作成"""
    # Flask-Session (SQLAlchemy backend) は自動でテーブルを作成するが、
    # 互換性のため、手動で作成できるロジックを残す
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id VARCHAR(256) PRIMARY KEY,
        data BYTEA NOT NULL,
        expiry TIMESTAMP WITHOUT TIME ZONE NOT NULL
    );
    """)

def create_tables(conn):
    """全てのテーブルを作成するメイン関数"""
    with conn.cursor() as cur:
        # データベース操作
        create_users_table(cur)
        create_games_table(cur)
        create_posts_table(cur)
        create_likes_table(cur)
        create_session_table(cur) # セッションテーブルを最後に作成
    
    conn.commit()
    print("Database tables created successfully! (users, games, posts, likes, sessions)")