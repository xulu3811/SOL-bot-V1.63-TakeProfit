"""Deterministic Decimal book formulas; none predict a guaranteed future price."""
from decimal import Decimal, localcontext

from .orderbook import decimal_value


def spread_metrics(bid, ask):
    bid, ask = decimal_value(bid), decimal_value(ask)
    if bid >= ask:
        raise ValueError("locked/crossed quotes are unavailable")
    with localcontext() as context:
        context.prec = 40
        spread = ask - bid
        midpoint = (bid + ask) / 2
        return {"spread": spread, "midpoint": midpoint, "spread_bps": spread / midpoint * Decimal(10000)}


def microprice(bid, ask, bid_qty, ask_qty):
    bid, ask = decimal_value(bid), decimal_value(ask)
    spread_metrics(bid, ask)
    bid_qty, ask_qty = decimal_value(bid_qty), decimal_value(ask_qty)
    with localcontext() as context:
        context.prec = 40
        return (ask * bid_qty + bid * ask_qty) / (bid_qty + ask_qty)


def depth_imbalance(bids, asks):
    if not bids or not asks:
        raise ValueError("both depth sides are required")
    with localcontext() as context:
        context.prec = 40
        buy = sum((decimal_value(q) for _, q in bids), Decimal(0))
        sell = sum((decimal_value(q) for _, q in asks), Decimal(0))
        return (buy - sell) / (buy + sell)
