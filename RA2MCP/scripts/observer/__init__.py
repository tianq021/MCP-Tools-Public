"""Observer-mode data collection (direction: watch AI-vs-AI, record everything).

This package is offline-first:
  * config.py   -- build/protect an isolated observer skirmish config (spawn.ini + spawnmap.ini)
  * extract.py  -- pure parsing: raw recording -> match/houses/build-orders/winner/first-defeat
  * store.py    -- SQLite schema + CSV exports
  * ingest_recording.py -- CLI: ingest an existing recorder output directory

The live collector (launch game + poll bridge) is intentionally NOT in this file yet;
it will be added after the offline pipeline passes and a real game launch is approved.
"""

__version__ = "0.1.0"
