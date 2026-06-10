"""Database migration: add status field to testcases table."""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'storage', 'app.db')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE testcases ADD COLUMN status VARCHAR(20) DEFAULT 'approved'")
    print("Column 'status' added successfully.")
except sqlite3.OperationalError as e:
    if 'duplicate column' in str(e).lower():
        print("Column 'status' already exists, skipping.")
    else:
        raise

cursor.execute("UPDATE testcases SET status = 'approved' WHERE status IS NULL OR status = 'pending'")
print(f"Updated {cursor.rowcount} existing rows to status='approved'.")

conn.commit()
conn.close()

print("Migration completed: status field added to testcases table.")