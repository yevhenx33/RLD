
import sqlite3
import shutil
import os
import sys
import time
from datetime import datetime
import logging

# Add backend directory to sys.path to import config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import DB_NAME

# --- CONFIG ---
BACKUP_ROOT = "backups"
RETENTION_DAYS = 7

# Logging Config
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Backup")

# Source Files (Adjusted for Scripts Directory: backend/scripts/)
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ROOT_DIR = os.path.abspath(os.path.join(BACKEND_DIR, '..'))

DB_FILE = os.path.join(BACKEND_DIR, DB_NAME)
CONFIG_FILE = os.path.join(BACKEND_DIR, "config.py")
ENV_FILE = os.path.join(ROOT_DIR, ".env")
YIELDS_FILE = os.path.join(BACKEND_DIR, "yields.json")

def create_backup():
    # 1. Setup Destination
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest_dir = os.path.join(ROOT_DIR, BACKUP_ROOT, timestamp)
    os.makedirs(dest_dir, exist_ok=True)
    
    logger.info(f"📦 Starting Backup to: {dest_dir}")
    
    # 2. SQLite Hot Backup (VACUUM INTO)
    # This creates a transaction-safe snapshot even if DB is busy.
    try:
        dest_db = os.path.join(dest_dir, DB_NAME)
        conn = sqlite3.connect(DB_FILE)
        logger.info(f"Snapshotting {DB_NAME}...")
        conn.execute(f"VACUUM INTO '{dest_db}'")
        conn.close()
        logger.info("✅ Database snapshot complete.")
    except Exception as e:
        logger.error(f"❌ Database snapshot failed (might be empty/locked): {e}")

    # 3. File Copies
    files_to_copy = [
        (CONFIG_FILE, "config.py"),
        (ENV_FILE, ".env"),
        (YIELDS_FILE, "yields.json")
    ]
    
    for src, name in files_to_copy:
        if os.path.exists(src):
            try:
                shutil.copy2(src, os.path.join(dest_dir, name))
                logger.info(f"✅ Copied {name}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to copy {name}: {e}")
        else:
            logger.warning(f"⚠️ Source not found: {name}")

    logger.info(f"🎉 Backup Complete: {timestamp}")
    
    # 4. Cleanup / Rotation
    cleanup_old_backups()

def cleanup_old_backups():
    root = os.path.join(ROOT_DIR, BACKUP_ROOT)
    if not os.path.exists(root):
        return
        
    logger.info("🧹 Checking for old backups...")
    
    # List all subdirectories
    backups = []
    for entry in os.scandir(root):
        if entry.is_dir():
            backups.append(entry)
    
    # Sort by name (timestamp)
    backups.sort(key=lambda x: x.name)
    
    # Retention Policy
    while len(backups) > RETENTION_DAYS:
        to_delete = backups.pop(0)
        logger.info(f"Deleting old backup: {to_delete.name}")
        shutil.rmtree(to_delete.path)

if __name__ == "__main__":
    create_backup()
