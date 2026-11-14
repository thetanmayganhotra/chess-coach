"""Chess coach web app. Run: ./run.sh  (or .venv/bin/python app.py) then open http://localhost:8765"""
import json, os, subprocess, sys, time
import chess
from flask import Flask, jsonify, request, send_from_directory
import coachlib as C

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, "static"))
conn = C.connect()
SYNC = {"proc": None, "log": os.path.join(HERE, "sync.log")}


def report():
    return json.load(open(os.path.join(HERE, "report.json")))


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/dashboard")
def dashboard():
    R = report()
    ratings = {r["time_class"]: dict(r) for r in conn.execute("SELECT * FROM ratings")}
    last_sync = conn.execute("SELECT ts, new_games FROM sync_log WHERE note='ok' ORDER BY ts DESC LIMIT 1").fetchone()
    return jsonify(ratings=ratings, history=C.rating_history(conn), weaknesses=C.weaknesses(R, 5),
                   abandoned=R["abandoned"], progress=C.progress(conn), by_time_class=R["by_time_class"],
                   tilt=R["tilt"], timeouts=R["timeouts"], moves=R["moves"], loss_phase=R["loss_phase"],
                   n_games=R["n_games"], puzzles=C.puzzle_stats(conn),
                   last_sync=dict(last_sync) if last_sync else None)


@app.get("/api/puzzle")
def puzzle():
    p = C.next_puzzle(conn, request.args.get("theme") or None)
    return jsonify(p or {"empty": True})


@app.post("/api/puzzle")
def puzzle_answer():
    d = request.get_json()
    return jsonify(C.answer_puzzle(conn, d["game_id"], int(d["ply"]), d["uci"]))


@app.get("/api/puzzle/themes")
def puzzle_themes():
    return jsonify([dict(r) for r in conn.execute(
        "SELECT theme, COUNT(*) n FROM puzzles WHERE theme IS NOT NULL GROUP BY theme ORDER BY n DESC")])


@app.get("/api/games")
def games():
    q = """SELECT g.id, g.date, g.time_class, g.color, g.opp_name, g.opp_rating, g.my_rating, g.result, g.termination,
                  g.opening, s.accuracy, s.blunders, s.mistakes FROM games g LEFT JOIN game_stats s ON s.game_id=g.id
           WHERE g.rules='chess'"""
    args = []
    for k in ("result", "time_class", "color"):
        if request.args.get(k):
            q += f" AND g.{k}=?"; args.append(request.args[k])
    if request.args.get("abandoned"):
        q += " AND g.my_result_code='abandoned'"
    q += " ORDER BY g.end_time DESC LIMIT 300"
    return jsonify([dict(r) for r in conn.execute(q, args)])


@app.get("/api/game/<gid>")
def game(gid):
    g = dict(conn.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone())
    a = conn.execute("SELECT final_cp FROM analysed WHERE game_id=?", (gid,)).fetchone()
    mine = {r["ply"]: r for r in conn.execute("SELECT * FROM my_moves WHERE game_id=?", (gid,))}
    board = chess.Board(); out = []
    rows = conn.execute("SELECT * FROM moves WHERE game_id=? ORDER BY ply", (gid,)).fetchall()
    for i, m in enumerate(rows):
        mv = chess.Move.from_uci(m["uci"])
        entry = {"ply": m["ply"], "san": m["san"], "uci": m["uci"], "fen_before": board.fen(), "cp": m["cp"], "mate": m["mate"]}
        board.push(mv)
        entry["fen_after"] = board.fen()
        entry["cp_after"] = rows[i + 1]["cp"] if i + 1 < len(rows) else (a["final_cp"] if a else None)
        if m["ply"] in mine:
            mm = mine[m["ply"]]
            entry.update(mine=True, cls=mm["cls"], missed_win=mm["missed_win"], best_uci=mm["best_uci"],
                         win_before=mm["win_before"], win_after=mm["win_after"])
            if mm["cls"] in ("blunder", "mistake", "inaccuracy") or mm["missed_win"]:
                entry["explain"] = C.explain(conn, gid, m["ply"])
        out.append(entry)
    g.pop("pgn"); g.pop("clocks")
    s = conn.execute("SELECT * FROM game_stats WHERE game_id=?", (gid,)).fetchone()
    return jsonify(game=g, stats=dict(s) if s else None, moves=out)


@app.get("/api/repertoire")
def repertoire():
    return jsonify(C.repertoire(conn))


@app.get("/api/plan")
def plan():
    return jsonify(C.plan(conn))


@app.post("/api/bot")
def bot():
    d = request.get_json()
    board = chess.Board(d["fen"])
    if d.get("uci"):
        mv = chess.Move.from_uci(d["uci"])
        if mv not in board.legal_moves:
            return jsonify(error="illegal"), 400
        your_san = board.san(mv); board.push(mv)
    else:
        your_san = None
    reply = None
    if not board.is_game_over():
        reply = C.bot_move(conn, board.fen(), float(d.get("strength", 1.0)))
        board.push(chess.Move.from_uci(reply["uci"]))
    res = None
    if board.is_game_over():
        o = board.outcome(); res = {"result": board.result(), "reason": o.termination.name.lower()}
    return jsonify(fen=board.fen(), your_san=your_san, reply=reply, legal=[m.uci() for m in board.legal_moves],
                   check=board.is_check(), over=res)


@app.post("/api/legal")
def legal():
    b = chess.Board(request.get_json()["fen"])
    return jsonify(legal=[m.uci() for m in b.legal_moves])


@app.post("/api/sync")
def sync_start():
    p = SYNC["proc"]
    if p and p.poll() is None:
        return jsonify(running=True)
    SYNC["proc"] = subprocess.Popen([sys.executable, os.path.join(HERE, "sync.py")], cwd=HERE,
                                    stdout=open(SYNC["log"], "w"), stderr=subprocess.STDOUT)
    return jsonify(running=True)


@app.get("/api/sync")
def sync_status():
    p = SYNC["proc"]
    running = bool(p and p.poll() is None)
    log = open(SYNC["log"]).read().strip().splitlines()[-1:] if os.path.exists(SYNC["log"]) else []
    failed = bool(p and p.poll() not in (None, 0))
    return jsonify(running=running, failed=failed, last=log[0] if log else None)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    print(f"Chess coach running at http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
