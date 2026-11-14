"""Run the production research replay on the six-month SOLUSDT store."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("year_bt", Path(r"D:\SOL-Bot\data\_year_backtest.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.DB = Path(r"D:\SOL-Bot\data\solusdt_6m.sqlite")
mod.OUT = Path(r"D:\SOL-Bot\data\solusdt_6m_backtest.json")
mod.main()
