# hatne_dict.py
import argparse
import sqlite3


def connect(db):
    return sqlite3.connect(db)


def init_db(args):
    with connect(args.db) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hatne TEXT UNIQUE NOT NULL,
            meaning TEXT NOT NULL
        )
        """)
    print("DB initialized")


def add_word(args):
    with connect(args.db) as conn:
        conn.execute(
            "INSERT INTO words (hatne, meaning) VALUES (?, ?)",
            (args.hatne, args.meaning)
        )
    print("Added")


def list_words(args):
    with connect(args.db) as conn:
        rows = conn.execute(
            "SELECT id, hatne, meaning FROM words ORDER BY id"
        ).fetchall()

    for r in rows:
        print(f"{r[0]}\t{r[1]}\t{r[2]}")


def show_word(args):
    with connect(args.db) as conn:
        row = conn.execute(
            "SELECT id, hatne, meaning FROM words WHERE hatne = ?",
            (args.hatne,)
        ).fetchone()

    if row:
        print(f"{row[0]}\t{row[1]}\t{row[2]}")
    else:
        print("Not found")


def update_word(args):
    with connect(args.db) as conn:
        cur = conn.execute(
            "UPDATE words SET meaning = ? WHERE hatne = ?",
            (args.meaning, args.hatne)
        )

    print("Updated" if cur.rowcount else "Not found")


def delete_word(args):
    with connect(args.db) as conn:
        cur = conn.execute(
            "DELETE FROM words WHERE hatne = ?",
            (args.hatne,)
        )

    print("Deleted" if cur.rowcount else "Not found")


def parse_args():
    parser = argparse.ArgumentParser("Hatne dictionary")
    parser.add_argument("--db", default="hatne.db")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.set_defaults(func=init_db)

    p = sub.add_parser("add")
    p.add_argument("--hatne", required=True)
    p.add_argument("--meaning", required=True)
    p.set_defaults(func=add_word)

    p = sub.add_parser("list")
    p.set_defaults(func=list_words)

    p = sub.add_parser("show")
    p.add_argument("--hatne", required=True)
    p.set_defaults(func=show_word)

    p = sub.add_parser("update")
    p.add_argument("--hatne", required=True)
    p.add_argument("--meaning", required=True)
    p.set_defaults(func=update_word)

    p = sub.add_parser("delete")
    p.add_argument("--hatne", required=True)
    p.set_defaults(func=delete_word)

    return parser.parse_args()


def main():
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()