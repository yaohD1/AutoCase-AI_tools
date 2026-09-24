from flask_sqlalchemy import SQLAlchemy
from flask import Flask
import sqlite3

db = SQLAlchemy()

def migrate_db(app: Flask):
    uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if not uri.startswith('sqlite:///'):
        return
    db_path = uri.replace('sqlite:///', '')
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(images)")
        cols = [r[1] for r in cur.fetchall()]
        if 'original_name' not in cols:
            cur.execute("ALTER TABLE images ADD COLUMN original_name VARCHAR(255)")
        cur.execute("PRAGMA table_info(testcases)")
        cols = [r[1] for r in cur.fetchall()]
        if 'image_id' not in cols:
            cur.execute("ALTER TABLE testcases ADD COLUMN image_id VARCHAR(36)")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_files'")
        if not cur.fetchone():
            cur.execute("""CREATE TABLE knowledge_files (
                id VARCHAR(36) PRIMARY KEY,
                project_id VARCHAR(36) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                original_name VARCHAR(255),
                file_path VARCHAR(500) NOT NULL,
                content TEXT,
                file_size INTEGER,
                created_at TIMESTAMP
            )""")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='automation_configs'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(automation_configs)")
            cols = [r[1] for r in cur.fetchall()]
            for name, definition in (
                ('opencode_model', "VARCHAR(200) DEFAULT ''"),
                ('max_heal_attempts', 'INTEGER DEFAULT 3')
            ):
                if name not in cols:
                    cur.execute(f"ALTER TABLE automation_configs ADD COLUMN {name} {definition}")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='automation_generations'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(automation_generations)")
            cols = [r[1] for r in cur.fetchall()]
            for name, definition in (
                ('stage', "VARCHAR(50) DEFAULT 'pending'"),
                ('requirement', 'TEXT'),
                ('event_log', 'TEXT'),
                ('test_result', 'TEXT'),
                ('heal_attempts', 'INTEGER DEFAULT 0'),
                ('event_cursor', 'INTEGER DEFAULT 0'),
                ('artifact_index', 'TEXT'),
                ('commit_status', "VARCHAR(20) DEFAULT 'uncommitted'"),
                ('commit_hash', 'VARCHAR(80)'),
                ('commit_message', 'VARCHAR(500)'),
                ('committed_files', 'TEXT'),
                ('started_at', 'TIMESTAMP'),
                ('process_id', 'INTEGER'),
                ('updated_at', 'TIMESTAMP')
            ):
                if name not in cols:
                    cur.execute(f"ALTER TABLE automation_generations ADD COLUMN {name} {definition}")
        conn.commit()
        conn.close()
    except Exception:
        pass

def init_db(app: Flask):
    db.init_app(app)
    migrate_db(app)
    with app.app_context():
        db.create_all()
