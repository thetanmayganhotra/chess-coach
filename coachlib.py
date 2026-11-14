"""Coaching logic shared by the web app and the sync job:
puzzles + spaced repetition, plain-English explanations, the 'you-style' bot,
opening repertoire, the 1500 plan and progress tracking."""
import json, math, os, random, sqlite3, threading, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
import chess, chess.engine

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "chess.db")
SF = "/opt/homebrew/bin/stockfish"
NAME = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
        chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}
VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}

SCHEMA = """
CREATE TABLE IF NOT EXISTS puzzles(game_id TEXT, ply INTEGER, fen TEXT, best_uci TEXT, played_uci TEXT,
  theme TEXT, kind TEXT, win_before REAL, dropw REAL, end_time INTEGER,
  ease REAL DEFAULT 2.5, ivl REAL DEFAULT 0, due INTEGER DEFAULT 0, reps INTEGER DEFAULT 0,
  lapses INTEGER DEFAULT 0, seen INTEGER DEFAULT 0, PRIMARY KEY(game_id, ply)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS puzzle_log(ts INTEGER, game_id TEXT, ply INTEGER, correct INTEGER, theme TEXT);
CREATE TABLE IF NOT EXISTS book(key TEXT, uci TEXT, n INTEGER, score REAL, PRIMARY KEY(key, uci)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS sync_log(ts INTEGER, new_games INTEGER, note TEXT);
"""

# Friendly names, why each one matters and what to do about it
WEAKNESS_INFO = {
    "Losing on time": ("Losing on time", "Mostly 3-minute games with no increment, often in positions you were winning.",
                       "Play 10-minute rapid. In blitz, once you're winning, trade pieces and move faster."),
    "Abandoning games": ("Abandoning games", "Leaving games, often right after a mistake, while there was still play.",
                         "Never leave a game. If you must go, resign. Opponents at your level blunder back a lot."),
    "Tactics: hung moved piece": ("Moving pieces to unsafe squares", "The piece you just moved can be taken for free.",
                                  "Before you let go of a piece: is that square attacked? Is it defended enough?"),
    "Tactics: left piece hanging": ("Leaving other pieces unprotected", "Your move stopped guarding something, or ignored a threat.",
                                    "After your opponent moves, ask: what do they attack now? Before you move: what was this piece guarding?"),
    "Tactics: fork": ("Walking into forks", "One enemy piece attacks two of yours at once.",
                      "Look for knight jumps and queen moves that hit two of your pieces, especially your king + something."),
    "Tactics: missed opponent tactic": ("Missing your opponent's threats", "Their best reply was a check or capture you didn't see coming.",
                                        "Each move, list every check and capture your opponent could play next."),
    "Tactics: missed free material": ("Missing free pieces", "Your opponent left something hanging and you didn't take it.",
                                      "Each move, first look at every capture you can make. Free stuff first."),
    "Tactics: allowed mate": ("Allowing checkmate", "Your move let your opponent force mate.",
                              "When your king is short of defenders, check every enemy check before moving."),
    "Tactics: missed mate": ("Missing checkmate", "You had a forced mate and played something else.",
                             "Always look at checks first. Checks, captures, threats, in that order."),
    "Tactics: back-rank mate": ("Back-rank mate", "Your king got trapped on the back row behind its own pawns.",
                                "Make a little escape square (h3/h6) once the position is calm."),
    "Tactics: pin": ("Pins", "A piece got stuck because moving it would expose something bigger.", "Watch for bishops and rooks lining up with your king or queen."),
    "Tactics: skewer / check tactic": ("Checks that win material", "A check that also attacks another piece.", "When your king and a piece are on the same line, see if a check hits both."),
    "Tactics: missed own tactic": ("Missing your own tactics", "You had a check or capture that won something.", "Look at every check and capture you can make before anything else."),
    "Tactics: positional / slow move": ("Slow moves in sharp spots", "Not a tactic, just a passive move when something active was needed.", "Ask what your worst piece is and improve it."),
}

TIPS = {
    "hung moved piece": "Before every move: is the square I'm going to attacked, and is it defended enough?",
    "left piece hanging": "Before every move: what is this piece guarding right now? And what does my opponent's last move attack?",
    "fork": "Look at every square an enemy knight or queen can jump to. Does any of them hit two of your pieces?",
    "pin": "When an enemy bishop, rook or queen lines up with your king or queen, the piece in between is stuck.",
    "skewer / check tactic": "If your king and another piece share a line, a check can win the piece behind.",
    "allowed mate": "When your king is short of defenders, look at every check your opponent has before you move.",
    "back-rank mate": "Your king had no escape square. Make luft (h3/h6 or g3/g6) when you have a quiet move.",
    "missed mate": "Look at checks first, every single move. That's how you find mates.",
    "missed free material": "Start every move by scanning for captures. If something is free, take it.",
    "missed opponent tactic": "Before you move, list your opponent's checks and captures in reply.",
    "missed own tactic": "Checks, captures, threats: look at all of yours before playing a 'normal' move.",
    "positional / slow move": "Nothing tactical, but your move was too slow. Ask what your opponent wants to do and stop it.",
}


def connect():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def wp(cp):
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self):
        self.lock = threading.Lock()
        self.e = None

    def get(self):
        if self.e is None:
            self.e = chess.engine.SimpleEngine.popen_uci(SF)
            self.e.configure({"Threads": 2, "Hash": 64})
        return self.e

    def analyse(self, board, depth=12, multipv=None):
        """multipv=None returns one info dict; a number returns a list of them."""
        kw = {"multipv": multipv} if multipv else {}
        with self.lock:
            try:
                return self.get().analyse(board, chess.engine.Limit(depth=depth), **kw)
            except chess.engine.EngineTerminatedError:
                self.e = None
                return self.get().analyse(board, chess.engine.Limit(depth=depth), **kw)

    def close(self):
        if self.e:
            self.e.quit(); self.e = None


ENGINE = Engine()
import atexit
atexit.register(ENGINE.close)


def mover_win(info, board):
    """win% for the side that just moved, from an analysis of the resulting position."""
    s = info["score"].pov(not board.turn)
    return wp(s.score(mate_score=10000))


# ---------------------------------------------------------------- explanations
def _sq(s):
    return chess.square_name(s)


def _line(board, ucis, n=4):
    b = board.copy(); out = []
    for u in ucis[:n]:
        m = chess.Move.from_uci(u)
        if m not in b.legal_moves:
            break
        out.append(b.san(m)); b.push(m)
    return out


def explain(conn, gid, ply):
    """Plain-English explanation of one of my moves."""
    mm = conn.execute("SELECT * FROM my_moves WHERE game_id=? AND ply=?", (gid, ply)).fetchone()
    if not mm:
        return None
    cur = conn.execute("SELECT * FROM moves WHERE game_id=? AND ply=?", (gid, ply)).fetchone()
    nxt = conn.execute("SELECT * FROM moves WHERE game_id=? AND ply=?", (gid, ply + 1)).fetchone()
    board = chess.Board(mm["fen"]); me = board.turn
    mv = chess.Move.from_uci(mm["uci"])
    after = board.copy(); after.push(mv)
    best = chess.Move.from_uci(mm["best_uci"]) if mm["best_uci"] else None
    best_san = board.san(best) if best and best in board.legal_moves else None
    best_line = _line(board, cur["pv"].split()) if cur and cur["pv"] else []
    reply_line = _line(after, nxt["pv"].split()) if nxt and nxt["pv"] else []
    reply = chess.Move.from_uci(nxt["pv"].split()[0]) if nxt and nxt["pv"] else None
    if reply and reply not in after.legal_moves:
        reply = None
    opp = "White" if after.turn == chess.WHITE else "Black"
    theme = mm["theme"]
    moved = after.piece_at(mv.to_square)
    s = []
    if theme == "hung moved piece" and reply:
        s.append(f"You put your {NAME[moved.piece_type]} on {_sq(mv.to_square)}, where it can simply be taken. "
                 f"{opp} plays {after.san(reply)} and wins it.")
    elif theme == "left piece hanging" and reply:
        v = after.piece_at(reply.to_square)
        what = NAME[v.piece_type] if v else "a piece"
        s.append(f"This left your {what} on {_sq(reply.to_square)} unprotected. {opp} takes it with {after.san(reply)}.")
    elif theme == "fork" and reply:
        b = after.copy(); b.push(reply)
        hit = [NAME[b.piece_at(q).piece_type] for q in b.attacks(reply.to_square)
               if b.piece_at(q) and b.piece_at(q).color == me and b.piece_at(q).piece_type != chess.PAWN]
        s.append(f"{after.san(reply)} is a fork: it attacks your {' and '.join(hit[:3]) or 'pieces'} at the same time, and you can only save one.")
    elif theme == "pin" and reply:
        s.append(f"{after.san(reply)} pins one of your pieces. It can't move without exposing something bigger behind it.")
    elif theme == "skewer / check tactic" and reply:
        s.append(f"{after.san(reply)} is a check that also hits something else. Once your king moves, you lose material.")
    elif theme in ("allowed mate", "back-rank mate"):
        s.append(f"This allows a forced checkmate: {' '.join(reply_line)}." if reply_line else "This allows a forced checkmate.")
        if theme == "back-rank mate":
            s.append("Your king was stuck on the back rank behind its own pawns.")
    elif theme == "missed mate":
        s.append(f"You had a forced checkmate here: {' '.join(best_line)}." if best_line else "You had a forced mate here.")
    elif theme == "missed free material" and best:
        v = board.piece_at(best.to_square)
        s.append(f"{best_san} just wins the {NAME[v.piece_type] if v else 'piece'} on {_sq(best.to_square)}. Your opponent left it hanging.")
    elif theme == "missed opponent tactic" and reply:
        s.append(f"You missed {opp}'s reply {after.san(reply)}. It's a {'check' if after.gives_check(reply) else 'capture'} that changes the position.")
    elif theme == "missed own tactic" and best_san:
        s.append(f"You had {best_san}, a {'check' if board.gives_check(best) else 'capture'} that wins something.")
    elif mm["missed_win"]:
        s.append(f"Your opponent had just blundered and {best_san} would have punished it.")
    elif mm["cls"]:
        s.append("Not a direct tactic, but a weaker move than it needed to be.")
    if mm["missed_win"] and theme not in ("missed mate", "missed free material"):
        s.append("Your opponent had just blundered, so you were winning before this move.")
    if best_san and best_san != mm["san"]:
        s.append(f"Better was {best_san}" + (f" (line: {' '.join(best_line)})" if len(best_line) > 1 else "") + ".")
    if reply_line and theme not in ("allowed mate", "back-rank mate"):
        s.append(f"Engine's punishment: {' '.join(reply_line)}.")
    s.append(f"Your winning chances went from {mm['win_before']:.0f}% to {mm['win_after']:.0f}%.")
    return {"text": " ".join(s), "tip": TIPS.get(theme), "theme": theme, "cls": mm["cls"],
            "best_san": best_san, "played_san": mm["san"], "missed_win": bool(mm["missed_win"])}


# ---------------------------------------------------------------- puzzles (spaced repetition)
def build_puzzles(conn):
    conn.execute("""INSERT OR IGNORE INTO puzzles(game_id, ply, fen, best_uci, played_uci, theme, kind, win_before, dropw, end_time)
        SELECT m.game_id, m.ply, m.fen, m.best_uci, m.uci, m.theme,
               CASE WHEN m.missed_win=1 THEN 'missed win' ELSE 'blunder' END, m.win_before, m.dropw, g.end_time
        FROM my_moves m JOIN games g ON g.id=m.game_id
        WHERE m.best_uci IS NOT NULL AND m.best_uci != m.uci
          AND ((m.cls='blunder' AND m.win_before >= 35) OR m.missed_win=1)""")
    conn.commit()


def top_themes(n=3):
    try:
        R = json.load(open(os.path.join(HERE, "report.json")))
        return [t for t, _, _ in R["theme_cost"][:n]]
    except Exception:
        return []


def puzzle_stats(conn):
    now = int(time.time())
    r = conn.execute("""SELECT COUNT(*) total, SUM(seen=0) fresh, SUM(seen=1 AND due<=?) due,
                          SUM(seen=1 AND reps>=3) learned FROM puzzles""", (now,)).fetchone()
    log = conn.execute("SELECT correct FROM puzzle_log ORDER BY ts DESC LIMIT 50").fetchall()
    today = conn.execute("SELECT COUNT(*) FROM puzzle_log WHERE ts>=?", (now - now % 86400,)).fetchone()[0]
    return {**dict(r), "recent_accuracy": round(100 * sum(x[0] for x in log) / len(log)) if log else None, "done_today": today}


def next_puzzle(conn, theme=None):
    now = int(time.time())
    where, args = "", []
    if theme:
        where, args = " AND theme=?", [theme]
    p = conn.execute(f"SELECT * FROM puzzles WHERE seen=1 AND due<=?{where} ORDER BY due LIMIT 1", [now] + args).fetchone()
    if not p:
        tops = top_themes() if not theme else [theme]
        order = "CASE " + " ".join(f"WHEN theme=? THEN {i}" for i in range(len(tops))) + f" ELSE {len(tops)} END," if tops else ""
        p = conn.execute(f"SELECT * FROM puzzles WHERE seen=0{where} ORDER BY {order} end_time DESC LIMIT 1", args + tops).fetchone()
    if not p:
        p = conn.execute(f"SELECT * FROM puzzles WHERE 1=1{where} ORDER BY due LIMIT 1", args).fetchone()
    if not p:
        return None
    g = conn.execute("SELECT opp_name, opp_rating, date, time_class, color, url FROM games WHERE id=?", (p["game_id"],)).fetchone()
    board = chess.Board(p["fen"])
    return {"game_id": p["game_id"], "ply": p["ply"], "fen": p["fen"], "turn": "white" if board.turn else "black",
            "legal": [m.uci() for m in board.legal_moves], "kind": p["kind"], "theme": p["theme"],
            "is_review": bool(p["seen"]), "lapses": p["lapses"],
            "context": f"vs {g['opp_name']} ({g['opp_rating']}), {g['time_class']}, {g['date'][:10]}, move {p['ply'] // 2 + 1}",
            "url": g["url"], "stats": puzzle_stats(conn)}


def answer_puzzle(conn, gid, ply, uci):
    p = conn.execute("SELECT * FROM puzzles WHERE game_id=? AND ply=?", (gid, ply)).fetchone()
    board = chess.Board(p["fen"])
    mv = chess.Move.from_uci(uci) if uci else None  # no move = "show answer", counts as a miss
    if mv and mv not in board.legal_moves:
        return {"error": "illegal move"}
    correct = uci == p["best_uci"]
    if mv and not correct and uci != p["played_uci"]:  # other moves that are just as good also count
        b = board.copy(); b.push(mv)
        your_win = mover_win(ENGINE.analyse(b, depth=12), b)
        correct = your_win >= p["win_before"] - 5 or your_win >= 90
    now = int(time.time())
    ease, ivl, reps, lapses = p["ease"], p["ivl"], p["reps"], p["lapses"]
    if correct:
        reps += 1
        ivl = 1 if reps == 1 else 3 if reps == 2 else ivl * ease
        ease = min(3.0, ease + 0.1)
        due = now + int(ivl * 86400)
    else:  # comes back in 10 minutes, then starts over
        reps, ivl, lapses = 0, 0, lapses + 1
        ease = max(1.3, ease - 0.2)
        due = now + 600
    conn.execute("UPDATE puzzles SET ease=?, ivl=?, due=?, reps=?, lapses=?, seen=1 WHERE game_id=? AND ply=?",
                 (ease, ivl, due, reps, lapses, gid, ply))
    conn.execute("INSERT INTO puzzle_log VALUES(?,?,?,?,?)", (now, gid, ply, int(correct), p["theme"]))
    conn.commit()
    best = chess.Move.from_uci(p["best_uci"])
    return {"correct": correct, "your_san": board.san(mv) if mv else None, "best_san": board.san(best), "best_uci": p["best_uci"],
            "played_in_game": board.san(chess.Move.from_uci(p["played_uci"])), "played_uci": p["played_uci"],
            "next_in_days": round(ivl, 1) if correct else None, "explain": explain(conn, gid, ply),
            "stats": puzzle_stats(conn)}


# ---------------------------------------------------------------- 'play against yourself' bot
def book_key(board):
    return board.epd()


def build_book(conn):
    """Every position where I had the move -> what I played there and how it went."""
    conn.execute("DELETE FROM book")
    agg = defaultdict(lambda: [0, 0.0])
    sc = {"win": 1, "draw": 0.5, "loss": 0}
    for gid, fen, uci, res in conn.execute("""SELECT m.game_id, m.fen, m.uci, g.result FROM my_moves m
                                              JOIN games g ON g.id=m.game_id WHERE m.ply < 30"""):
        k = " ".join(fen.split()[:4])
        a = agg[(k, uci)]; a[0] += 1; a[1] += sc[res]
    conn.executemany("INSERT INTO book VALUES(?,?,?,?)", [(k, u, n, s / n) for (k, u), (n, s) in agg.items()])
    conn.commit()


def style_profile(conn):
    """How often I blunder / err, by phase — the bot copies this."""
    out = {}
    for ph in ("opening", "middlegame", "endgame"):
        r = conn.execute("""SELECT COUNT(*) n, SUM(cls='blunder') b, SUM(cls='mistake') m, SUM(cls='inaccuracy') i
                            FROM my_moves WHERE phase=?""", (ph,)).fetchone()
        n = max(1, r["n"])
        out[ph] = {"blunder": (r["b"] or 0) / n, "mistake": (r["m"] or 0) / n, "inaccuracy": (r["i"] or 0) / n}
    return out


_PROFILE = None


def _phase(board):
    pieces = sum(1 for p in board.piece_map().values() if p.piece_type not in (chess.PAWN, chess.KING))
    return "endgame" if pieces <= 6 else "opening" if board.fullmove_number <= 12 else "middlegame"


def bot_move(conn, fen, strength=1.0):
    """Pick a move the way I would: my own opening moves first, then an engine that makes my kind of mistakes.
    strength scales my error rates (1.0 = me, 0.5 = half as many mistakes)."""
    global _PROFILE
    board = chess.Board(fen)
    if board.is_game_over():
        return None
    rows = conn.execute("SELECT uci, n, score FROM book WHERE key=?", (book_key(board),)).fetchall()
    rows = [r for r in rows if chess.Move.from_uci(r["uci"]) in board.legal_moves]
    if rows:
        r = random.choices(rows, weights=[x["n"] for x in rows])[0]
        mv = chess.Move.from_uci(r["uci"])
        return {"uci": r["uci"], "san": board.san(mv),
                "why": f"Your book move. You played this {r['n']} time{'s' if r['n'] > 1 else ''} here and scored {r['score'] * 100:.0f}%."}
    _PROFILE = _PROFILE or style_profile(conn)
    p = _PROFILE[_phase(board)]
    infos = ENGINE.analyse(board, depth=10, multipv=6)
    cands = []
    for inf in infos:
        if "pv" not in inf:
            continue
        cands.append((inf["pv"][0], wp(inf["score"].pov(board.turn).score(mate_score=10000))))
    best_w = cands[0][1]
    roll = random.random()
    bl, mi, ina = p["blunder"] / strength, p["mistake"] / strength, p["inaccuracy"] / strength
    if roll < bl:
        # my signature blunder: put a piece on a square where it can just be taken
        hang = []
        for m in board.legal_moves:
            pc = board.piece_at(m.from_square)
            if pc.piece_type in (chess.PAWN, chess.KING) or board.is_capture(m):
                continue
            b = board.copy(); b.push(m)
            if b.is_attacked_by(b.turn, m.to_square) and (not b.is_attacked_by(not b.turn, m.to_square) or any(
                    VAL[b.piece_at(a).piece_type] < VAL[pc.piece_type] for a in b.attackers(b.turn, m.to_square))):
                hang.append(m)
        if hang:
            m = random.choice(hang)
            return {"uci": m.uci(), "san": board.san(m), "why": "Your style: a blunder. This puts a piece where it can be taken (your most common mistake)."}
        m = cands[-1][0]
        return {"uci": m.uci(), "san": board.san(m), "why": "Your style: a blunder (the worst of the engine's top moves)."}
    if roll < bl + mi + ina and len(cands) > 1:
        pool = [c for c in cands[1:] if best_w - c[1] >= 5] or cands[1:]
        m = random.choice(pool)[0]
        return {"uci": m.uci(), "san": board.san(m), "why": "Your style: an imprecise move, not the engine's first choice."}
    m = cands[0][0]
    return {"uci": m.uci(), "san": board.san(m), "why": "Solid move."}


# ---------------------------------------------------------------- opening repertoire
def _seq_stats(conn, color, first=None, plies=6):
    out = defaultdict(lambda: [0, 0.0])
    sc = {"win": 1, "draw": 0.5, "loss": 0}
    for res, ucis in conn.execute("""SELECT g.result, (SELECT group_concat(san, ' ') FROM
            (SELECT san FROM moves WHERE game_id=g.id ORDER BY ply LIMIT ?)) FROM games g
            WHERE g.color=? AND g.rules='chess'""", (plies, color)):
        if not ucis:
            continue
        if first and not ucis.startswith(first + " "):
            continue
        a = out[ucis]; a[0] += 1; a[1] += sc[res]
    return out


def _tree(conn, color, first, depth):
    rows = _seq_stats(conn, color, first, depth)
    lines = sorted(((k, n, s / n) for k, (n, s) in rows.items() if n >= 3), key=lambda x: -x[1])[:6]
    return [{"line": k, "games": n, "score": round(100 * s)} for k, n, s in lines]


def repertoire(conn):
    def score(where, args=()):
        r = conn.execute(f"""SELECT COUNT(*) n, AVG(CASE result WHEN 'win' THEN 1 WHEN 'draw' THEN .5 ELSE 0 END) s
                             FROM games g WHERE rules='chess' AND {where}""", args).fetchone()
        return {"games": r["n"], "score": round(100 * (r["s"] or 0))}
    def opening(col, w, b=None):
        q = "color=? AND EXISTS(SELECT 1 FROM moves WHERE game_id=g.id AND ply=0 AND san=?)"
        if b:
            q += " AND EXISTS(SELECT 1 FROM moves WHERE game_id=g.id AND ply=1 AND san=?)"
        return score(q, (col, w, b) if b else (col, w))
    # positions in the first 12 moves where I keep making the same mistake
    trouble = []
    for r in conn.execute("""SELECT substr(m.fen, 1, instr(m.fen, ' ') + 1) k, m.fen, m.san, m.best_uci, m.theme, g.color,
            COUNT(*) n, AVG(m.dropw) d, MIN(m.game_id) gid, MIN(m.ply) ply
            FROM my_moves m JOIN games g ON g.id=m.game_id
            WHERE m.ply < 24 AND m.cls IN ('mistake','blunder') GROUP BY k, m.san HAVING n >= 2 ORDER BY n * d DESC LIMIT 12"""):
        b = chess.Board(r["fen"])
        best = chess.Move.from_uci(r["best_uci"]) if r["best_uci"] else None
        trouble.append({"fen": r["fen"], "color": r["color"], "played": r["san"], "times": r["n"],
                        "avg_drop": round(r["d"]), "best": b.san(best) if best and best in b.legal_moves else None,
                        "best_uci": r["best_uci"], "theme": r["theme"], "line": _line_to(conn, r["gid"], r["ply"]),
                        "game_id": r["gid"], "ply": r["ply"]})
    return {
        "white": {
            "pick": "1.d4, then a London setup (Nf3, Bf4, e3, Bd3, c3, O-O)",
            "why": "1.d4 is already your best weapon. The London is the same setup against almost everything, so there's less to memorise and fewer early traps.",
            "stats": opening("white", "d4"), "lines": _tree(conn, "white", "d4", 6),
            "notes": ["Against 1...e5 (Englund Gambit): 2.dxe5 Nc6 3.Nf3 Qe7 4.Nc3. Don't play an early Bf4 here, because ...Qb4+ hits b2.",
                      "Against 1...d5 or 1...Nf6: 2.Bf4 or 2.Nf3, then e3, Bd3, c3. Castle before you attack.",
                      "Don't jump your knight to b5 early unless it actually wins something."]},
        "black_e4": {
            "pick": "1...e5, and 2...Nc6 against nearly everything",
            "why": "You already play 1...e5 most often. Developing the queen's knight first defends e5 and stops the early queen tricks that keep beating you.",
            "stats": opening("black", "e4", "e5"), "lines": _tree(conn, "black", "e4", 6),
            "notes": ["Against 2.Qh5: 2...Nc6! 3.Bc4 g6 4.Qf3 Nf6. Never 2...g6?? because 3.Qxe5+ forks your king and rook.",
                      "Against 2.Nf3 Nc6 3.Bc4 (Italian): 3...Bc5 or 3...Nf6, and castle quickly. Watch for Ng5 hitting f7.",
                      "Against 2.Bc4: 2...Nf6, hitting e4.",
                      "Stop playing the Scandinavian (1...d5) for now. It scores badly for you."]},
        "black_d4": {
            "pick": "1...d5 and ...Bf5, then ...e6, ...Nf6, ...Bd6/Be7, ...O-O",
            "why": "You already get your light-squared bishop out to f5 before ...e6, and those lines score well for you. Keep it, and castle early.",
            "stats": opening("black", "d4"), "lines": _tree(conn, "black", "d4", 6),
            "notes": ["Against the London (2.Bf4): ...Nf6, ...Bf5, ...e6, ...c5. Same plan, just with ...c5.",
                      "Don't take on c4 and try to keep the pawn. Develop instead."]},
        "trouble": trouble,
    }


def _line_to(conn, gid, ply):
    sans = [r[0] for r in conn.execute("SELECT san FROM moves WHERE game_id=? AND ply<? ORDER BY ply", (gid, ply))]
    return " ".join((f"{i // 2 + 1}. " if i % 2 == 0 else "") + s for i, s in enumerate(sans))


# ---------------------------------------------------------------- progress + plan
def window_stats(conn, t0, t1):
    r = conn.execute("""SELECT COUNT(*) n,
        AVG(CASE result WHEN 'win' THEN 1 WHEN 'draw' THEN .5 ELSE 0 END) score,
        AVG(s.accuracy) acc, AVG(s.blunders) blunders,
        AVG(my_result_code='abandoned') abandon, AVG(result='loss' AND termination='timeout') timeout
        FROM games g LEFT JOIN game_stats s ON s.game_id=g.id
        WHERE rules='chess' AND end_time>=? AND end_time<?""", (t0, t1)).fetchone()
    if not r["n"]:
        return None
    f = lambda v, k=1: round(v * k, 1) if v is not None else None
    return {"games": r["n"], "score": f(r["score"], 100), "accuracy": f(r["acc"]), "blunders_per_game": f(r["blunders"], 1),
            "abandon_pct": f(r["abandon"], 100), "timeout_pct": f(r["timeout"], 100)}


def progress(conn):
    now = int(time.time()); d = 86400
    return {"last_30": window_stats(conn, now - 30 * d, now + d), "prev_30": window_stats(conn, now - 60 * d, now - 30 * d),
            "all_time": window_stats(conn, 0, now + d)}


def rating_history(conn):
    out = defaultdict(list)
    for tc, t, r in conn.execute("SELECT time_class, end_time, my_rating FROM games WHERE rules='chess' AND rated=1 ORDER BY end_time"):
        out[tc].append([t, r])
    return out


def milestones(conn):
    rapid = conn.execute("SELECT rating FROM ratings WHERE time_class='rapid'").fetchone()
    start = rapid[0] if rapid else 400
    start_t = conn.execute("SELECT MIN(ts) FROM sync_log").fetchone()[0] or int(time.time())
    d0 = datetime.fromtimestamp(start_t)
    base = conn.execute("SELECT note FROM sync_log WHERE note LIKE 'plan_start:%' LIMIT 1").fetchone()
    if base:
        start = int(base[0].split(":")[1])
    else:
        conn.execute("INSERT INTO sync_log VALUES(?,?,?)", (int(time.time()), 0, f"plan_start:{start}")); conn.commit()
    # quick wins first (stop abandoning, stop flagging), then slower steady gains
    gains = [100, 90, 80] + [70] * 30
    out, r, y, m = [], start, d0.year, d0.month
    actual = {}
    for t, rt in conn.execute("SELECT end_time, my_rating FROM games WHERE time_class='rapid' AND rated=1 ORDER BY end_time"):
        dt = datetime.fromtimestamp(t); actual[(dt.year, dt.month)] = rt
    for g in gains:
        m += 1
        if m > 12:
            m, y = 1, y + 1
        r = min(1500, r + g)
        out.append({"month": f"{y}-{m:02d}", "target": r, "actual": actual.get((y, m))})
        if r >= 1500:
            break
    return {"start": start, "months": out}


def plan(conn):
    R = json.load(open(os.path.join(HERE, "report.json")))
    themes = [t for t, _, _ in R["theme_cost"][:3]]
    nice = {t: WEAKNESS_INFO.get(f"Tactics: {t}", (t,))[0].lower() for t in themes}
    tilt = R["tilt"]["by_loss_streak"]
    return {
        "rules": [
            {"rule": "Never abandon a game. If you have to go, resign first.",
             "why": f"You've abandoned {R['abandoned']['count']} games, {R['abandoned']['pct_of_losses']:.0f}% of your losses. When you kept playing from a losing position you still won about a third."},
            {"rule": "Rated games: 10-minute rapid only. No blitz until rapid hits 600.",
             "why": f"Rapid is your best time control. {R['timeouts']['lost_on_time']} timeout losses, {R['timeouts']['while_winning']} of them in winning positions, mostly in 3-minute games."},
            {"rule": "Stop after 2 losses in a row. Do 10 puzzles instead.",
             "why": f"You score {tilt['0']['score_pct']:.0f}% normally, {tilt['1']['score_pct']:.0f}% after one loss and {tilt['2']['score_pct']:.0f}% after two."},
            {"rule": "Max 5 rated games per session.",
             "why": "Fewer, focused games beat a long session. Review one loss instead of playing game 6."},
            {"rule": "Before every move: checks, captures, threats, for BOTH sides.",
             "why": "Most of your lost rating comes from pieces left en prise and tactics you didn't see."},
        ],
        "daily": [
            f"15 min: My Blunders puzzles (prioritised: {', '.join(nice.values())})",
            "2–3 rated 10-minute rapid games",
            "Review your last loss in Game Review (5 min)",
        ],
        "week": [
            {"day": "Mon", "focus": "Puzzles: " + (nice.get(themes[0], "tactics") if themes else "tactics"), "play": "3 rapid"},
            {"day": "Tue", "focus": "Openings: drill your trouble positions", "play": "2 rapid"},
            {"day": "Wed", "focus": "Puzzles: " + (nice.get(themes[1], "tactics") if len(themes) > 1 else "tactics"), "play": "2 games vs Bot-You, then 2 rapid"},
            {"day": "Thu", "focus": "Review 2 losses fully", "play": "2 rapid"},
            {"day": "Fri", "focus": "Puzzles: " + (nice.get(themes[2], "tactics") if len(themes) > 2 else "tactics"), "play": "3 rapid"},
            {"day": "Sat", "focus": "Longer session: 30 min puzzles", "play": "1 × 15|10 or 30-minute game, really slow"},
            {"day": "Sun", "focus": "Rest or Sync + check progress", "play": "optional"},
        ],
        "milestones": milestones(conn),
    }


def weaknesses(R, n=5):
    out = []
    for w in R["weaknesses"][:n]:
        name, what, fix = WEAKNESS_INFO.get(w["name"], (w["name"], "", ""))
        out.append({"name": name, "cost": w["rating_cost"], "detail": w["detail"], "what": what, "fix": fix})
    return out
