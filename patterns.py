"""Turn raw engine evals into coaching insight.

Builds the `my_moves` table (one row per move I played: win% before/after, class, phase,
theme, clock) and writes report.json with all pattern stats. Safe to re-run any time.
"""
import io, json, math, os, sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
import chess, chess.pgn

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "chess.db")
VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
# win% drop thresholds (0-100 scale)
BLUNDER, MISTAKE, INACC = 30, 20, 10
LOCAL_TZ = datetime.now().astimezone().tzinfo


def wp(cp):  # Lichess win-probability model
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_acc(drop):  # Lichess per-move accuracy
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * max(drop, 0)) - 3.1669))


def base_seconds(tc):
    if not tc or "/" in tc:
        return None
    return int(tc.split("+")[0])


def phase_of(board):
    pieces = sum(1 for p in board.piece_map().values() if p.piece_type not in (chess.PAWN, chess.KING))
    if pieces <= 6:
        return "endgame"
    if board.fullmove_number <= 12:
        return "opening"
    return "middlegame"


def opening_family(name):
    if not name:
        return "Unknown"
    words = name.split()
    for i, w in enumerate(words):
        if w in ("Defense", "Opening", "Gambit", "Game", "Attack", "System", "Countergambit"):
            return " ".join(words[: i + 1])
    return " ".join(words[:3])


def safe_capture(board, mv):
    """True if mv captures something worth more than what's at risk (crude SEE)."""
    if not board.is_capture(mv):
        return False
    victim = board.piece_at(mv.to_square)
    if victim is None:  # en passant
        return False
    attacker = board.piece_at(mv.from_square)
    defended = board.is_attacked_by(not board.turn, mv.to_square)
    return VAL[victim.piece_type] >= 3 and (not defended or VAL[victim.piece_type] > VAL[attacker.piece_type])


def theme_for(before, my_mv, after, opp_pv, my_best, my_mate, opp_mate, me):
    """Name the tactical reason a move was bad. before/after are boards around my move."""
    # 1. walked into a forced mate
    if opp_mate is not None and opp_mate != 0 and ((opp_mate > 0) != (me == chess.WHITE)):
        b = after.copy()
        for m in opp_pv:
            if m not in b.legal_moves:
                break
            b.push(m)
            if b.is_checkmate():
                k = b.king(me)
                last = b.peek()
                piece = b.piece_at(last.to_square)
                back = 0 if me == chess.WHITE else 7
                if chess.square_rank(k) == back and piece and piece.piece_type in (chess.ROOK, chess.QUEEN) \
                        and chess.square_rank(last.to_square) == back:
                    return "back-rank mate"
                break
        return "allowed mate"
    # 2. had a forced mate and missed it
    if my_mate is not None and my_mate != 0 and ((my_mate > 0) == (me == chess.WHITE)):
        return "missed mate"
    reply = opp_pv[0] if opp_pv else None
    # 3. opponent's best reply simply wins material I left en prise
    if reply and reply in after.legal_moves and safe_capture(after, reply):
        return "hung moved piece" if reply.to_square == my_mv.to_square else "left piece hanging"
    # 4. I could have won material and didn't
    if my_best and my_best != my_mv and my_best in before.legal_moves and safe_capture(before, my_best):
        return "missed free material"
    if reply and reply in after.legal_moves:
        b = after.copy()
        b.push(reply)
        p = b.piece_at(reply.to_square)
        if p:
            targets = [sq for sq in b.attacks(reply.to_square)
                       if b.piece_at(sq) and b.piece_at(sq).color == me and VAL[b.piece_at(sq).piece_type] >= 3]
            if len(targets) >= 2 and any(VAL[b.piece_at(s).piece_type] > VAL[p.piece_type] or
                                         not b.is_attacked_by(me, reply.to_square) for s in targets):
                return "fork"
            for sq in chess.SquareSet(b.occupied_co[me]):
                if b.is_pinned(me, sq) and not after.is_pinned(me, sq) and b.piece_at(sq).piece_type != chess.PAWN:
                    return "pin"
            if p.piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN) and b.is_check():
                return "skewer / check tactic"
        if after.is_capture(reply) or after.gives_check(reply):
            return "missed opponent tactic"
    if my_best and my_best in before.legal_moves and (before.is_capture(my_best) or before.gives_check(my_best)):
        return "missed own tactic"
    return "positional / slow move"


def build():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.executescript("""DROP TABLE IF EXISTS my_moves;
    CREATE TABLE my_moves(game_id TEXT, ply INTEGER, fen TEXT, san TEXT, uci TEXT, best_uci TEXT,
      win_before REAL, win_after REAL, dropw REAL, cls TEXT, phase TEXT, theme TEXT,
      clock REAL, clock_frac REAL, missed_win INTEGER, spent REAL, PRIMARY KEY(game_id, ply)) WITHOUT ROWID;
    DROP TABLE IF EXISTS game_stats;
    CREATE TABLE game_stats(game_id TEXT PRIMARY KEY, accuracy REAL, n_my INTEGER, blunders INTEGER,
      mistakes INTEGER, inaccuracies INTEGER, final_win REAL, decisive_ply INTEGER, decisive_drop REAL,
      decisive_win_before REAL, decisive_phase TEXT, decisive_theme TEXT, win10 REAL, first_err_ply INTEGER,
      first_err_san TEXT);""")
    games = db.execute("""SELECT g.*, a.final_cp FROM games g JOIN analysed a ON a.game_id=g.id""").fetchall()
    for g in games:
        moves = db.execute("SELECT * FROM moves WHERE game_id=? ORDER BY ply", (g["id"],)).fetchall()
        me = chess.WHITE if g["color"] == "white" else chess.BLACK
        sign = 1 if me == chess.WHITE else -1
        cps = [m["cp"] for m in moves] + [g["final_cp"]]
        clocks = json.loads(g["clocks"] or "[]")
        base = base_seconds(g["time_control"])
        board = chess.Board()
        rows, accs = [], []
        win10 = None
        for i, m in enumerate(moves):
            if i == 20 + (0 if me == chess.WHITE else 1) or (win10 is None and i == len(moves) - 1 and i >= 20):
                win10 = wp(sign * cps[i])
            mv = chess.Move.from_uci(m["uci"])
            if board.turn == me:
                wb, wa = wp(sign * cps[i]), wp(sign * cps[i + 1])
                drop = max(0.0, wb - wa)
                cls = "blunder" if drop >= BLUNDER else "mistake" if drop >= MISTAKE else "inaccuracy" if drop >= INACC else None
                prev_w = wp(sign * cps[i - 1]) if i > 0 else 50
                missed = int(wb >= 80 and prev_w < 60 and wa < 60)
                clk = clocks[i - 2] if i >= 2 and len(clocks) > i - 2 else base
                inc = int(g["time_control"].split("+")[1]) if "+" in (g["time_control"] or "") else 0
                spent = round(clk + inc - clocks[i], 1) if clk is not None and len(clocks) > i else None
                after = board.copy(); after.push(mv)
                theme = None
                if drop >= MISTAKE or missed:
                    nxt = moves[i + 1] if i + 1 < len(moves) else None
                    opp_pv = [chess.Move.from_uci(u) for u in (nxt["pv"].split() if nxt and nxt["pv"] else [])]
                    my_best = chess.Move.from_uci(m["best_uci"]) if m["best_uci"] else None
                    theme = theme_for(board, mv, after, opp_pv, my_best, m["mate"], nxt["mate"] if nxt else None, me)
                rows.append((g["id"], i, board.fen(), m["san"], m["uci"], m["best_uci"], round(wb, 1), round(wa, 1),
                             round(drop, 1), cls, phase_of(board), theme, clk,
                             round(clk / base, 3) if clk is not None and base else None, missed, spent))
                accs.append(move_acc(drop))
            board.push(mv)
        db.executemany("INSERT INTO my_moves VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        dec = max(rows, key=lambda r: r[8]) if rows else None
        first = next((r for r in rows if r[9] in ("mistake", "blunder")), None)
        db.execute("INSERT INTO game_stats VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            g["id"], round(sum(accs) / len(accs), 1) if accs else None, len(rows),
            sum(r[9] == "blunder" for r in rows), sum(r[9] == "mistake" for r in rows),
            sum(r[9] == "inaccuracy" for r in rows), round(wp(sign * cps[-1]), 1),
            dec[1] if dec else None, dec[8] if dec else None, dec[6] if dec else None,
            dec[10] if dec else None, dec[11] if dec else None, win10,
            first[1] if first else None, first[3] if first else None))
    db.commit()
    return db


def pct(a, b):
    return round(100 * a / b, 1) if b else None


def report(db):
    G = [dict(r) for r in db.execute("""SELECT g.*, s.* FROM games g JOIN game_stats s ON s.game_id=g.id
                                         ORDER BY g.end_time""")]
    allg = [dict(r) for r in db.execute("SELECT * FROM games WHERE rules='chess' ORDER BY end_time")]
    M = [dict(r) for r in db.execute("SELECT m.*, g.time_class FROM my_moves m JOIN games g ON g.id=m.game_id")]
    gid = {g["id"]: g for g in G}
    R = {"n_games": len(allg), "n_analysed": len(G)}

    # rating delta per game (rating listed is post-game) + per-class K estimate
    last = {}
    for g in allg:
        tc = g["time_class"]
        g["delta"] = g["my_rating"] - last[tc] if tc in last and g["rated"] else None
        last[tc] = g["my_rating"]
    for g in G:
        g["delta"] = next(x["delta"] for x in allg if x["id"] == g["id"])
    K = {}
    for tc in {g["time_class"] for g in allg}:
        d = [abs(g["delta"]) for g in allg if g["time_class"] == tc and g["delta"] is not None and g["result"] != "draw"]
        K[tc] = 2 * (sum(d) / len(d)) if d else 16
    R["k_estimate"] = {k: round(v, 1) for k, v in K.items()}

    def score(r):
        return {"win": 1, "draw": 0.5, "loss": 0}[r]

    def cost(g, win_before):  # rating lost vs the expected result at that moment
        return K[g["time_class"]] * max(0, win_before / 100 - score(g["result"]))

    # --- move classification overall
    mine = [m for m in M]
    R["moves"] = {"total": len(mine), **{c: sum(m["cls"] == c for m in mine) for c in ("blunder", "mistake", "inaccuracy")},
                  "missed_wins": sum(m["missed_win"] for m in mine)}
    R["moves"]["blunders_per_game"] = round(R["moves"]["blunder"] / max(1, len(G)), 2)

    # --- phase where losses happen (decisive move of games that weren't already lost)
    losses = [g for g in G if g["result"] == "loss" and g["decisive_win_before"] is not None]
    ph = Counter(g["decisive_phase"] for g in losses if g["decisive_win_before"] >= 40 and g["decisive_drop"] >= MISTAKE)
    R["loss_phase"] = dict(ph)
    R["blunders_by_phase"] = {p: {"moves": sum(m["phase"] == p for m in mine),
                                  "blunders": sum(m["phase"] == p and m["cls"] == "blunder" for m in mine)} for p in ("opening", "middlegame", "endgame")}
    for p, v in R["blunders_by_phase"].items():
        v["per_100_moves"] = pct(v["blunders"], v["moves"])

    # --- themes
    th = Counter(m["theme"] for m in mine if m["theme"] and m["cls"] in ("mistake", "blunder"))
    R["themes"] = th.most_common()
    th_cost = defaultdict(float)
    th_games = Counter()
    for g in G:
        if g["decisive_theme"] and g["decisive_drop"] >= MISTAKE and g["result"] != "win":
            th_cost[g["decisive_theme"]] += cost(g, g["decisive_win_before"])
            th_games[g["decisive_theme"]] += 1
    R["theme_cost"] = sorted(((t, round(c), th_games[t]) for t, c in th_cost.items()), key=lambda x: -x[1])
    R["missed_wins_cost"] = round(sum(cost(gid[m["game_id"]], m["win_before"]) for m in mine if m["missed_win"]
                                      and gid[m["game_id"]]["result"] != "win") )

    # --- openings
    op = defaultdict(lambda: {"n": 0, "w": 0, "d": 0, "l": 0, "win10": [], "first_err": Counter(), "acc": []})
    for g in G:
        o = op[(g["color"], opening_family(g["opening"]))]
        o["n"] += 1; o[g["result"][0]] += 1
        if g["win10"] is not None: o["win10"].append(g["win10"])
        if g["first_err_ply"] is not None and g["first_err_ply"] < 24: o["first_err"][f"{g['first_err_ply']//2+1}. {g['first_err_san']}"] += 1
        if g["accuracy"]: o["acc"].append(g["accuracy"])
    R["openings"] = sorted([{"color": c, "opening": n, "games": o["n"], "win_rate": pct(o["w"] + 0.5 * o["d"], o["n"]),
                             "avg_win_pct_move10": round(sum(o["win10"]) / len(o["win10"]), 1) if o["win10"] else None,
                             "accuracy": round(sum(o["acc"]) / len(o["acc"]), 1) if o["acc"] else None,
                             "common_early_errors": o["first_err"].most_common(3)}
                            for (c, n), o in op.items() if o["n"] >= 8], key=lambda x: -x["games"])

    # --- time trouble
    buckets = [("<10% clock", 0, .1), ("10-25%", .1, .25), ("25-50%", .25, .5), (">50%", .5, 9)]
    R["time_trouble"] = []
    for name, lo, hi in buckets:
        mm = [m for m in mine if m["clock_frac"] is not None and lo <= m["clock_frac"] < hi]
        R["time_trouble"].append({"bucket": name, "moves": len(mm), "blunder_per_100": pct(sum(m["cls"] == "blunder" for m in mm), len(mm)),
                                  "avg_accuracy": round(sum(move_acc(m["dropw"]) for m in mm) / len(mm), 1) if mm else None})
    # think time: how long I spent on the move vs how often it was a blunder (non-trivial positions only)
    R["think_time"] = []
    for name, lo, hi in (("<2s", 0, 2), ("2-5s", 2, 5), ("5-15s", 5, 15), ("15s+", 15, 1e9)):
        mm = [m for m in mine if m["spent"] is not None and lo <= m["spent"] < hi and 10 < m["win_before"] < 90]
        R["think_time"].append({"bucket": name, "moves": len(mm), "pct_of_moves": None,
                                "blunder_per_100": pct(sum(m["cls"] == "blunder" for m in mm), len(mm)),
                                "mistake_or_worse_per_100": pct(sum(m["cls"] in ("blunder", "mistake") for m in mm), len(mm))})
    tot = sum(b["moves"] for b in R["think_time"])
    for b in R["think_time"]:
        b["pct_of_moves"] = pct(b["moves"], tot)
    bl = [m for m in mine if m["cls"] == "blunder" and m["spent"] is not None]
    R["blunders_played_under_5s_pct"] = pct(sum(m["spent"] < 5 for m in bl), len(bl))
    to = [g for g in G if g["result"] == "loss" and g["termination"] == "timeout"]
    R["timeouts"] = {"lost_on_time": len(to), "pct_of_losses": pct(len(to), sum(g["result"] == "loss" for g in G)),
                     "while_winning": sum(g["final_win"] >= 70 for g in to), "while_equal": sum(30 < g["final_win"] < 70 for g in to),
                     "rating_cost": round(sum(cost(g, g["final_win"]) for g in to))}

    # --- tilt & sessions (session = gap < 30 min)
    prev = None; streak = 0; sess = 0
    for g in allg:
        if prev and g["end_time"] - prev["end_time"] < 1800:
            sess += 1
        else:
            sess = 1; streak = 0
        g["sess_idx"] = sess; g["streak_before"] = streak
        g["after"] = prev["result"] if prev and g["end_time"] - prev["end_time"] < 1800 else None
        streak = streak + 1 if g["result"] == "loss" else 0
        g["hour"] = datetime.fromtimestamp(g["end_time"], timezone.utc).astimezone(LOCAL_TZ).hour
        prev = g
    accmap = {g["id"]: g["accuracy"] for g in G}

    def summarize(gs):
        a = [accmap[g["id"]] for g in gs if accmap.get(g["id"])]
        return {"games": len(gs), "score_pct": pct(sum(score(g["result"]) for g in gs), len(gs)),
                "accuracy": round(sum(a) / len(a), 1) if a else None,
                "abandon_pct": pct(sum(g["my_result_code"] == "abandoned" for g in gs), len(gs))}
    R["tilt"] = {
        "after_win": summarize([g for g in allg if g["after"] == "win"]),
        "after_loss": summarize([g for g in allg if g["after"] == "loss"]),
        "by_loss_streak": {k: summarize([g for g in allg if (g["streak_before"] if g["streak_before"] < 3 else 3) == k]) for k in range(4)},
        "by_session_game": {lbl: summarize([g for g in allg if lo <= g["sess_idx"] <= hi])
                            for lbl, lo, hi in (("1-3", 1, 3), ("4-6", 4, 6), ("7-10", 7, 10), ("11+", 11, 999))},
        "by_hour": {lbl: summarize([g for g in allg if g["hour"] in hrs]) for lbl, hrs in (
            ("morning 6-12", range(6, 12)), ("afternoon 12-18", range(12, 18)), ("evening 18-23", range(18, 23)),
            ("late night 23-6", [23, 0, 1, 2, 3, 4, 5]))},
    }
    R["by_time_class"] = {tc: summarize([g for g in allg if g["time_class"] == tc]) for tc in ("bullet", "blitz", "rapid", "daily")}
    for tc, v in R["by_time_class"].items():
        rs = [g["my_rating"] for g in allg if g["time_class"] == tc]
        v["rating_now"] = rs[-1] if rs else None; v["rating_peak"] = max(rs) if rs else None
        v["opp_avg"] = round(sum(g["opp_rating"] for g in allg if g["time_class"] == tc) / len(rs)) if rs else None

    # --- abandoned
    ab = [g for g in allg if g["my_result_code"] == "abandoned"]
    abG = [gid[g["id"]] for g in ab if g["id"] in gid]
    for g in abG:
        mm = [m for m in mine if m["game_id"] == g["id"]]
        g["blunder_last3"] = any(m["cls"] == "blunder" for m in sorted(mm, key=lambda m: m["ply"])[-3:])
    full = {g["id"]: g for g in allg}
    R["abandoned"] = {
        "count": len(ab), "pct_of_all_games": pct(len(ab), len(allg)),
        "pct_of_losses": pct(len(ab), sum(g["result"] == "loss" for g in allg)),
        "state_when_left": {"winning (>=70%)": sum(g["final_win"] >= 70 for g in abG),
                            "equal": sum(30 < g["final_win"] < 70 for g in abG),
                            "losing (<=30%)": sum(g["final_win"] <= 30 for g in abG)},
        "left_right_after_a_blunder": sum(g["blunder_last3"] for g in abG),
        "moves_played": {"0-5": sum(g["n_moves"] // 2 <= 5 for g in ab), "6-20": sum(5 < g["n_moves"] // 2 <= 20 for g in ab),
                         "21+": sum(g["n_moves"] // 2 > 20 for g in ab)},
        "by_time_class": dict(Counter(g["time_class"] for g in ab)),
        "abandon_rate_by_time_class": {tc: pct(sum(g["my_result_code"] == "abandoned" for g in allg if g["time_class"] == tc),
                                               sum(g["time_class"] == tc for g in allg)) for tc in ("bullet", "blitz", "rapid")},
        "by_hour": dict(sorted(Counter(full[g["id"]]["hour"] for g in ab).items())),
        "losses_in_a_row_before": dict(sorted(Counter(min(full[g["id"]]["streak_before"], 4) for g in ab).items())),
        "session_game_index_avg": round(sum(full[g["id"]]["sess_idx"] for g in ab) / max(1, len(ab)), 1),
        "rating_points_lost_actual": -sum(full[g["id"]]["delta"] or 0 for g in ab),
        "rating_cost_vs_position": round(sum(cost(g, g["final_win"]) for g in abG)),
    }
    def my_last_clock_frac(g):
        c = json.loads(g["clocks"] or "[]"); base = base_seconds(g["time_control"])
        mine_c = c[0::2] if g["color"] == "white" else c[1::2]
        return mine_c[-1] / base if mine_c and base else None
    fr = [x for x in (my_last_clock_frac(g) for g in ab) if x is not None]
    R["abandoned"]["clock_left_when_left"] = {">75%": sum(x > .75 for x in fr), "25-75%": sum(.25 <= x <= .75 for x in fr),
                                              "<25%": sum(x < .25 for x in fr)}
    R["abandoned"]["avg_opp_rating_gap"] = round(sum(g["opp_rating"] - g["my_rating"] for g in ab) / max(1, len(ab)))
    R["abandoned"]["avg_opp_rating_gap_all_games"] = round(sum(g["opp_rating"] - g["my_rating"] for g in allg) / len(allg))
    R["abandoned"]["win_pct_drop_in_last_5_moves"] = round(sum(
        max(0, (max([m["win_before"] for m in mine if m["game_id"] == g["id"]][-5:] or [50])) - g["final_win"]) for g in abG) / max(1, len(abG)), 1)
    R["abandoned"]["opponent_abandoned"] =sum(g["opp_result_code"] == "abandoned" for g in allg)

    # --- weaknesses ranked by rating cost
    weak = [("Abandoning games", R["abandoned"]["rating_cost_vs_position"] + 0, f"{len(ab)} games abandoned"),
            ("Losing on time", R["timeouts"]["rating_cost"], f"{len(to)} timeouts, {R['timeouts']['while_winning']} while winning")]
    weak += [(f"Tactics: {t}", c, f"decided {n} games") for t, c, n in R["theme_cost"]]
    R["weaknesses"] = sorted(({"name": n, "rating_cost": c, "detail": d} for n, c, d in weak), key=lambda x: -x["rating_cost"])
    R["ratings"] = [dict(r) for r in db.execute("SELECT * FROM ratings")]
    json.dump(R, open(os.path.join(HERE, "report.json"), "w"), indent=1, default=str)
    return R


if __name__ == "__main__":
    R = report(build())
    print(json.dumps(R, indent=1, default=str))
