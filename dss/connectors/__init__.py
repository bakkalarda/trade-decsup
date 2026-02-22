"""Data connectors — fetch raw market data from external sources.

Available connectors:
  - price            — ccxt (crypto), yfinance (traditional)
  - macro            — DXY, SPX, VIX, Gold, MOVE, credit, real yields (yfinance + FRED)
  - derivatives      — funding, OI via ccxt + DeFiLlama + Binance Futures
  - binance_futures  — L/S ratios, taker buy/sell, liquidations, OI history (free, no key)
  - options          — Deribit (crypto), yfinance (traditional)
  - onchain          — Blockchain.com (BTC), CoinGecko (global)
  - sentiment        — Alternative.me (F&G), CoinGecko (trending)
  - news             — CryptoPanic headline aggregation + sentiment (free tier)
  - defillama        — TVL, stablecoin supply, DEX volumes (free)
"""
