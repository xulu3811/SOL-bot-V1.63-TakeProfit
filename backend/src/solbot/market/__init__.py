"""Public SOLUSDT market-data ingestion. No trading or credential interfaces."""

from .backfill import Backfill, detect_gaps, history_start
from .collector import Collector
from .rest import AccessBlocked, BinanceRest, MarketDataError, MarketUnavailable

__all__ = ["AccessBlocked", "Backfill", "BinanceRest", "Collector", "MarketDataError", "MarketUnavailable", "detect_gaps", "history_start"]
