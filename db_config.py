import os
import psycopg2
from psycopg2 import extras
from dotenv import load_dotenv

load_dotenv()

def get_db_url():
    """
    環境変数からPostgreSQLの接続URLを取得し、SQLAlchemy形式（postgresql://）に変換する。
    """
    db_url = os.environ.get('DATABASE_URL')
    
    if db_url is None:
        db_url = os.environ.get('DATABASE_URL_LOCAL')
        if db_url is None:
            raise ValueError("DATABASE_URL environment variable is not set.")
    
    # Renderのpsycopg2形式 (postgres://) を SQLAlchemy形式 (postgresql://) に変換
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql://", 1)
        
    return db_url

def get_db_connection():
    """
    psycopg2でデータベースに接続する。
    """
    db_url_raw = os.environ.get('DATABASE_URL')
    if db_url_raw is None:
        db_url_raw = os.environ.get('DATABASE_URL_LOCAL')
        if db_url_raw is None:
            raise ValueError("DATABASE_URL environment variable is not set.")
            
    # Render環境に合わせてSSLモードを設定
    conn = psycopg2.connect(db_url_raw, sslmode='require' if 'render.com' in db_url_raw else 'disable')
    return conn

def create_tables(conn):
    """
    アプリケーションに必要なテーブルを作成する。
    """
    cursor = conn.cursor()
    
    # usersテーブル
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(80) UNIQUE NOT NULL,
            password_hash VARCHAR(128) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # gamesテーブル: カラム名を game_url に統一
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            game_url TEXT, -- <<<<<<<<<<<<<<<< 修正: url -> game_url
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # postsテーブル
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id SERIAL PRIMARY KEY,
            game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            content TEXT,
            media_url VARCHAR(255),
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # likesテーブル
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id SERIAL PRIMARY KEY,
            post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE (post_id, user_id)
        );
    """)

    # sessionsテーブル (Flask-Session用)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id VARCHAR(256) PRIMARY KEY,
            data TEXT,
            expiry TIMESTAMP WITHOUT TIME ZONE
        );
    """)
    
    conn.commit()
    cursor.close()
