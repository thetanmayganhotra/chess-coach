# Chess Coach (AAMKAAADMI)

A personal chess coach that runs on your Mac. It pulls all your Chess.com games, analyses them with Stockfish, finds your real weaknesses, and turns your own blunders into puzzles.

Run it:  `./run.sh`  → opens http://localhost:8765

The first run creates a Python virtualenv (`chess`, `requests`, `flask`), installs Stockfish with Homebrew if missing, and does the first sync. Your games are stored in `chess.db` (SQLite, not in the repo). To use it for your own account, change `USER` at the top of `fetch.py`.

| Screen | What it does |
|---|---|
| Dashboard | Ratings, rating graph, last-30-days vs previous, top 5 weaknesses by rating cost, abandoned-games analysis, tilt |
| My Blunders | Puzzles from your own games (position before a blunder / missed win). Spaced repetition: wrong → back in 10 min, right → 1, 3, ~8+ days |
| Openings | Repertoire for White / Black vs e4 / Black vs d4 from your stats, plus positions where you repeat the same mistake (click to drill) |
| Game Review | Any game, with eval bar + graph, mistakes in red/green arrows and a plain-English explanation |
| 1500 Plan | House rules, daily + weekly routine, monthly rapid targets vs actual |
| Play Yourself | Bot that plays your opening moves, then an engine that makes your kind of mistakes at your rate |

Sync: the button in the header, plus a daily job at 07:00 (`install_daily_sync.sh`; log in `sync-daily.log`).

Files: `fetch.py` (Chess.com → SQLite), `analyze.py` (Stockfish, resumable), `patterns.py` (stats → report.json),
`coachlib.py` (puzzles, explanations, bot, plan), `app.py` (web server), `static/` (front end). Data: `chess.db`.

Re-run analysis deeper: `.venv/bin/python analyze.py 16 8` only analyses games not yet done; delete rows from `analysed` to redo.
