import os
import psycopg2
import psycopg2.extras

# Renderの環境変数からDB接続情報を取得
# RenderのPostgresで使うための接続関数に変更
def get_db_connection():
    # Renderは接続URLを'DATABASE_URL'という環境変数で提供する
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        # ローカル開発環境用（Renderでは使用されない）
        raise ValueError("DATABASE_URL環境変数が設定されていません。")

    # psycopg2で接続URLをパースして接続を確立
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.DictCursor)
    # MySQLのcommit設定に合わせて、autocommitをFalseに設定
    conn.autocommit = False
    return conn