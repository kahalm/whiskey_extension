import os
import time

import pymysql
import pymysql.cursors


def get_connection():
    config = {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": os.environ.get("DB_USER", "whisky"),
        "password": os.environ.get("DB_PASSWORD", "whisky"),
        "database": os.environ.get("DB_NAME", "whiskybase"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }
    for attempt in range(10):
        try:
            return pymysql.connect(**config)
        except pymysql.err.OperationalError:
            if attempt < 9:
                time.sleep(3)
            else:
                raise


def init_db(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS whiskies (
                wbid INT PRIMARY KEY,
                name TEXT,
                brand_name TEXT,
                distillery TEXT,
                district TEXT,
                country TEXT,
                age TEXT,
                strength TEXT,
                size TEXT,
                bottler TEXT,
                bottling_serie TEXT,
                cask_type TEXT,
                cask_number TEXT,
                barcode TEXT,
                vintage TEXT,
                bottled TEXT,
                category TEXT,
                rating DOUBLE,
                votes INT,
                image_url TEXT,
                url TEXT,
                detail_scraped INT DEFAULT 0,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col, typedef in [("barcode", "TEXT"), ("detail_scraped", "INT DEFAULT 0"), ("shop_price", "TEXT"), ("shop_count", "INT")]:
            try:
                cur.execute(f"ALTER TABLE whiskies ADD COLUMN {col} {typedef}")
            except pymysql.err.OperationalError as e:
                if e.args[0] != 1060:  # Duplicate column name
                    raise
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_state (
                id INT PRIMARY KEY CHECK (id = 1),
                last_wbid INT NOT NULL DEFAULT 0
            )
        """)
        cur.execute("INSERT IGNORE INTO scrape_state (id, last_wbid) VALUES (1, 0)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS search_state (
                id INT PRIMARY KEY CHECK (id = 1),
                last_query VARCHAR(255) NOT NULL DEFAULT ''
            )
        """)
        cur.execute("INSERT IGNORE INTO search_state (id, last_query) VALUES (1, '')")
        # releases_state: migrate old single-row schema if needed
        cur.execute("""
            CREATE TABLE IF NOT EXISTS releases_state (
                filter_key VARCHAR(64) PRIMARY KEY,
                last_year INT NOT NULL DEFAULT 0
            )
        """)
        cur.execute("SHOW COLUMNS FROM releases_state LIKE 'id'")
        if cur.fetchone():
            cur.execute("SELECT last_year FROM releases_state LIMIT 1")
            old_row = cur.fetchone()
            old_year = old_row["last_year"] if old_row else 0
            cur.execute("DROP TABLE releases_state")
            cur.execute("""
                CREATE TABLE releases_state (
                    filter_key VARCHAR(64) PRIMARY KEY,
                    last_year INT NOT NULL DEFAULT 0
                )
            """)
            if old_year:
                cur.execute(
                    "INSERT INTO releases_state (filter_key, last_year) VALUES ('default', %s)",
                    (old_year,),
                )
    conn.commit()


DETAIL_VERSION = 3


def save_whisky(conn, data: dict):
    """Save full detail data (Phase 2). Overwrites all fields."""
    data = {**data, "detail_scraped": DETAIL_VERSION}
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    with conn.cursor() as cur:
        cur.execute(
            f"REPLACE INTO whiskies ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
    conn.commit()


def save_whisky_basic(conn, data: dict):
    """Save basic search data (Phase 1). Only inserts if WBID doesn't exist yet."""
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT IGNORE INTO whiskies ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
    conn.commit()


def has_wbid(conn, wbid: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM whiskies WHERE wbid = %s", (wbid,))
        return cur.fetchone() is not None


def get_last_wbid(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT last_wbid FROM scrape_state WHERE id = 1")
        row = cur.fetchone()
        return row["last_wbid"] if row else 0


def set_last_wbid(conn, wbid: int):
    with conn.cursor() as cur:
        cur.execute("UPDATE scrape_state SET last_wbid = %s WHERE id = 1", (wbid,))
    conn.commit()


def get_search_state(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT last_query FROM search_state WHERE id = 1")
        row = cur.fetchone()
        return row["last_query"] if row else ""


def set_search_state(conn, query: str):
    with conn.cursor() as cur:
        cur.execute("UPDATE search_state SET last_query = %s WHERE id = 1", (query,))
    conn.commit()


def get_releases_state(conn, filter_key: str = "default") -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT last_year FROM releases_state WHERE filter_key = %s", (filter_key,))
        row = cur.fetchone()
        return row["last_year"] if row else 0


def set_releases_state(conn, year: int, filter_key: str = "default"):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM releases_state WHERE filter_key = %s", (filter_key,))
        if cur.fetchone():
            cur.execute(
                "UPDATE releases_state SET last_year = %s WHERE filter_key = %s",
                (year, filter_key),
            )
        else:
            cur.execute(
                "INSERT INTO releases_state (filter_key, last_year) VALUES (%s, %s)",
                (filter_key, year),
            )
    conn.commit()


def get_all_releases_states(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT filter_key, last_year FROM releases_state ORDER BY filter_key")
        return cur.fetchall()


def get_whisky_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) as cnt FROM whiskies")
        return cur.fetchone()["cnt"]


def get_unscraped_wbids(conn) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT wbid FROM whiskies WHERE detail_scraped < %s "
            "ORDER BY rating DESC, bottled DESC, wbid DESC",
            (DETAIL_VERSION,),
        )
        return [row["wbid"] for row in cur.fetchall()]


def get_detail_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) as cnt FROM whiskies WHERE detail_scraped = 1")
        return cur.fetchone()["cnt"]
