"""Pull new games, analyse them, refresh patterns/puzzles/book. Used by the Sync button and the daily job."""
import time
import fetch, analyze, patterns, coachlib


def run():
    t0 = time.time()
    print("Fetching new games from Chess.com…", flush=True)
    new = fetch.main()
    print("Analysing new games with Stockfish…", flush=True)
    analyze.main()
    print("Updating your patterns…", flush=True)
    patterns.report(patterns.build())
    conn = coachlib.connect()
    coachlib.build_puzzles(conn)
    coachlib.build_book(conn)
    conn.execute("INSERT INTO sync_log VALUES(?,?,?)", (int(time.time()), new, "ok"))
    conn.commit()
    print(f"SYNC DONE: {new} new games in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    run()
