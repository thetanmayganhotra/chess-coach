"""Stockfish analysis of every game, most recent first. Resumable: finished games are skipped.

Stores one row per ply: the engine eval of the position BEFORE the move (white POV),
the engine's best move and a short PV. The eval after a move = eval before the next ply.
Usage: analyze.py [depth] [workers]
"""
import io, os, sqlite3, sys, time
from multiprocessing import Pool
import chess, chess.engine, chess.pgn

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chess.db")
DEPTH = int(sys.argv[1]) if len(sys.argv) > 1 else 14
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
SF = "/opt/homebrew/bin/stockfish"

SCHEMA = """
CREATE TABLE IF NOT EXISTS moves(
  game_id TEXT, ply INTEGER, uci TEXT, san TEXT,
  cp INTEGER, mate INTEGER, best_uci TEXT, pv TEXT,
  PRIMARY KEY(game_id, ply)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS analysed(game_id TEXT PRIMARY KEY, depth INTEGER,
  final_cp INTEGER, final_mate INTEGER, at INTEGER);
"""

_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = chess.engine.SimpleEngine.popen_uci(SF)
        _engine.configure({"Threads": 1, "Hash": 32})
    return _engine


def score(info, board):
    s = info["score"].white()
    if s.is_mate():
        return (10000 if s.mate() > 0 else -10000), s.mate()
    if board.is_checkmate():  # side to move is mated
        return (-10000 if board.turn else 10000), 0
    return s.score(), None


def analyse_game(args):
    gid, pgn = args
    game = chess.pgn.read_game(io.StringIO(pgn))
    board = game.board()
    rows = []
    eng = engine()
    for ply, mv in enumerate(game.mainline_moves()):
        info = eng.analyse(board, chess.engine.Limit(depth=DEPTH))
        cp, mate = score(info, board)
        pv = info.get("pv", [])
        rows.append((gid, ply, mv.uci(), board.san(mv), cp, mate,
                     pv[0].uci() if pv else None, " ".join(m.uci() for m in pv[:6])))
        board.push(mv)
    if board.is_game_over():
        o = board.outcome()
        fcp = 0 if o.winner is None else (10000 if o.winner else -10000)
        fm = 0 if o.winner is not None else None
    else:
        fcp, fm = score(eng.analyse(board, chess.engine.Limit(depth=DEPTH)), board)
    return gid, rows, fcp, fm


def main():
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    todo = db.execute("""SELECT id, pgn FROM games WHERE rules='chess' AND pgn != ''
        AND id NOT IN (SELECT game_id FROM analysed) ORDER BY end_time DESC""").fetchall()
    total = len(todo)
    print(f"{total} games to analyse at depth {DEPTH} with {WORKERS} workers", flush=True)
    t0 = time.time()
    with Pool(WORKERS) as pool:
        for i, (gid, rows, fcp, fm) in enumerate(pool.imap_unordered(analyse_game, todo, chunksize=2), 1):
            db.execute("DELETE FROM moves WHERE game_id=?", (gid,))
            db.executemany("INSERT INTO moves VALUES(?,?,?,?,?,?,?,?)", rows)
            db.execute("INSERT OR REPLACE INTO analysed VALUES(?,?,?,?,?)", (gid, DEPTH, fcp, fm, int(time.time())))
            db.commit()
            if i % 25 == 0 or i == total:
                el = time.time() - t0
                print(f"{i}/{total}  {el/60:.1f} min elapsed, ~{el/i*(total-i)/60:.1f} min left", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
