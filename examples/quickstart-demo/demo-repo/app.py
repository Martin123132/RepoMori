import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path="notes.sqlite"):
        self.path = Path(path)

    def connect(self):
        return sqlite3.connect(str(self.path))

    def setup(self):
        with self.connect() as conn:
            conn.execute("create table if not exists notes (title text, body text)")

    def save_note(self, title, body):
        self.setup()
        with self.connect() as conn:
            conn.execute("insert into notes values (?, ?)", (title, body))

    def list_titles(self):
        self.setup()
        with self.connect() as conn:
            rows = conn.execute("select title from notes order by title").fetchall()
        return [row[0] for row in rows]


def main():
    store = Store()
    store.save_note("repomori", "machine-readable repository memory")
    return store.list_titles()
