"""Trading strategies — self-contained, individually backtestable signal
models that feed the strategy gate (see agents/COUNCIL_DESIGN.md). Each
strategy is domain-isolated to the market data it needs and emits the same
{stance, confidence, reasons} shape the gate consumes.
"""
