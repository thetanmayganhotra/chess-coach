"""Pull games + ratings from the public Chess.com API into SQLite.

Re-runnable: completed past months are skipped; the current month is always refreshed.
"""
import io, json, re, sqlite3, sys, time
from datetime import datetime, timezone
import chess.pgn
import requests

USER = "aamkaaadmi"
DB = __file__.rsplit("/", 1)[0] + "/chess.db"
UA = "chess-coach/0.1 (personal training tool)"
S = requests.Session()
S.headers["User-Agent"] = UA

SCHEMA = """
CREATE TABLE IF NOT EXISTS games(
  id TEXT PRIMARY KEY, url TEXT, end_time INTEGER, date TEXT,
  time_class TEXT, time_control TEXT, rated INTEGER, rules TEXT,
  color TEXT, my_rating INTEGER, opp_rating INTEGER, opp_name TEXT,
  result TEXT, my_result_code TEXT, opp_result_code TEXT, termination TEXT,
  eco TEXT, opening TEXT, n_moves INTEGER, clocks TEXT, pgn TEXT);
CREATE TABLE IF NOT EXISTS archives(url TEXT PRIMARY KEY, fetched_at INTEGER, done INTEGER);
CREATE TABLE IF NOT EXISTS ratings(time_class TEXT PRIMARY KEY, rating INTEGER, best INTEGER,
  wins INTEGER, losses INTEGER, draws INTEGER, fetched_at INTEGER);
CREATE INDEX IF NOT EXISTS g_end ON games(end_time);
"""

LOSS = {"checkmated", "resigned", "timeout", "abandoned", "lose", "bughousepartnerlose", "kingofthehill", "threecheck"}
DRAW = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}


def get(url):
    for attempt in range(5):
        r = S.get(url, timeout=30)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate limited: {url}")


def parse(g):
    me_white = g["white"]["username"].lower() == USER
    me, opp = (g["white"], g["black"]) if me_white else (g["black"], g["white"])
    mc, oc = me["result"], opp["result"]
    if mc == "win":
        result, how = "win", oc          # how the opponent lost
    elif mc in DRAW or oc in DRAW and mc not in LOSS:
        result, how = "draw", mc
    else:
        result, how = "loss", mc
    pgn = g.get("pgn", "")
    eco = opening = None; n = 0; clocks = []
    if pgn:
        h = chess.pgn.read_headers(io.StringIO(pgn))
        eco = h.get("ECO")
        ourl = h.get("ECOUrl", "")
        opening = ourl.rsplit("/", 1)[-1].replace("-", " ") if ourl else None
        clocks = re.findall(r"\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]", pgn)
        clocks = [round(int(a) * 3600 + int(b) * 60 + float(c), 1) for a, b, c in clocks]
        n = len(clocks)
    end = g.get("end_time", 0)
    return dict(
        id=g["uuid"], url=g["url"], end_time=end,
        date=datetime.fromtimestamp(end, timezone.utc).isoformat(),
        time_class=g.get("time_class"), time_control=g.get("time_control"),
        rated=int(g.get("rated", False)), rules=g.get("rules"),
        color="white" if me_white else "black",
        my_rating=me.get("rating"), opp_rating=opp.get("rating"), opp_name=opp.get("username"),
        result=result, my_result_code=mc, opp_result_code=oc, termination=how,
        eco=eco, opening=opening, n_moves=n, clocks=json.dumps(clocks), pgn=pgn)


def main():
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    archives = get(f"https://api.chess.com/pub/player/{USER}/games/archives")["archives"]
    now_m = datetime.now(timezone.utc).strftime("%Y/%m")
    new = 0
    for url in archives:
        row = db.execute("SELECT done FROM archives WHERE url=?", (url,)).fetchone()
        if row and row[0] and not url.endswith(now_m):
            continue
        games = get(url)["games"]
        for g in games:
            p = parse(g)
            cur = db.execute(f"INSERT OR IGNORE INTO games({','.join(p)}) VALUES({','.join('?'*len(p))})", list(p.values()))
            new += cur.rowcount
        # the current month is still filling up, so only mark past months as done
        db.execute("INSERT OR REPLACE INTO archives VALUES(?,?,?)", (url, int(time.time()), int(not url.endswith(now_m))))
        db.commit()
        print(f"{url[-7:]}: {len(games)} games", flush=True)
        time.sleep(0.5)  # one at a time, polite
    stats = get(f"https://api.chess.com/pub/player/{USER}/stats")
    for k, v in stats.items():
        if k.startswith("chess_") and "last" in v:
            rec = v.get("record", {})
            db.execute("INSERT OR REPLACE INTO ratings VALUES(?,?,?,?,?,?,?)",
                       (k[6:], v["last"]["rating"], v.get("best", {}).get("rating"),
                        rec.get("win"), rec.get("loss"), rec.get("draw"), int(time.time())))
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    print(f"new games: {new}, total: {total}")
    for r in db.execute("SELECT * FROM ratings"):
        print(r)
    return new


if __name__ == "__main__":
    main()
