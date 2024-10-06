# Momentum Trading System - Hyperliquid

A funding-aware, volatility-scaled time-series momentum (TSMOM) strategy for Hyperliquid perpetuals.

## 📖 How It Works (Simple Explanation)

This system automatically trades perpetual futures using a momentum strategy. Here's what it does in plain English:

### The Core Idea: Ride the Trends

**Momentum trading** is simple: assets that went up recently tend to keep going up (and vice versa). This system:
1. Looks at which coins are trending strongly
2. Buys the uptrending ones, shorts the downtrending ones
3. Sizes positions based on risk (not dollars)
4. Rebalances periodically to stay aligned with fresh trends

### What Makes This Strategy Smart

**1. Multi-Speed Detection**
- Doesn't just look at one timeframe (e.g., "30 days")
- Combines short, medium, and long lookbacks (10d, 30d, 90d)
- Averages them to catch trends at different speeds
- More reliable than any single timeframe

**2. Funding-Aware**
- Perpetual futures charge hourly "funding rates" (like borrowing costs)
- If a long trend would cost you 5% in funding over your holding period, the system reduces that position
- Only trades when trend strength > fees + funding costs
- Avoids expensive carries that kill profits

**3. Risk-First Sizing**
- Volatile coins get smaller positions, stable coins get larger positions
- Each position contributes similar risk to your portfolio
- Targets a specific portfolio volatility (e.g., 15% annual)
- Prevents one wild asset from dominating your risk

**4. Smart Execution**
- Tries to be a "maker" (post limit orders) to earn rebates instead of paying fees
- For large trades, uses TWAP (Time-Weighted Average Price) to minimize market impact
- For testing: uses instant market orders to see results immediately
- Handles position flips safely (close existing, then open new)

**5. Safety Rails**
- **Kill Switch**: Halts trading if daily losses exceed threshold or API errors pile up
- **Edge Gate**: Only trades when expected profit > 2-3× the costs
- **Liquidity Caps**: Won't try to trade more than X% of daily volume
- **ATR Stops**: Exits if price moves too far against you

### A Trade Example

1. **Signal**: BTC's 10d, 30d, 90d returns are all strongly positive → generates a +1.5 signal
2. **Funding Check**: BTC funding is +0.01%/hour (longs pay shorts) → penalizes the signal slightly to +1.3
3. **Risk Sizing**: BTC has 60% annual volatility; system allocates 2.17% of portfolio (= 1.3 / 60%)
4. **Portfolio Scaling**: Scales all weights so total portfolio volatility = 15%/year
5. **Edge Check**: Expected edge (signal × ATR) = 3.2× costs (fees + slippage) → passes
6. **Execution**: Places maker limit order at best bid; if not filled in 10s, switches to market/TWAP

### Real-World Flow

**Every rebalance (daily, 4h, or 1h):**
1. Fetch the top 40 most liquid perpetuals
2. Compute momentum signals for each (z-scored returns across multiple lookbacks)
3. Adjust for funding costs (penalize expensive carries)
4. Size positions to equalize risk
5. Filter out trades where edge < friction
6. Execute orders (maker-first, market for testing)
7. Monitor fills and update positions

**Between rebalances:**
- System sleeps until next scheduled time
- Kill switch monitors for losses/errors
- Positions ride the trends

## 📐 Specification

Full technical specification: [`Spec.md`](./Spec.md)

## 🏗️ Architecture

```
momentum_trading/
├── core/               # Core system (config, scheduler, orchestrator)
│   ├── config.py       # Configuration management
│   └── scheduler.py    # Rebalance scheduling (daily/4h)
│
├── data/               # Market data loading
│   └── loader.py       # Candles, funding, L2 book, universe
│
├── signals/            # Signal generation
│   └── engine.py       # TSMOM z-scores + funding penalties
│
├── risk/               # Portfolio construction
│   └── engine.py       # Vol targeting, beta neutralization, caps
│
├── execution/          # Order routing
│   ├── orders.py       # OrderIntent, ExecutionReport
│   └── router.py       # ALO/TWAP/IOC routing + reduce-only logic
│
├── monitoring/         # Observability
│   ├── metrics.py      # SLO tracking (maker %, funding PnL, etc.)
│   └── kill_switch.py  # Circuit breakers
│
├── utils/              # Helpers
│   ├── precision.py    # Tick/lot formatting (≤5 sig figs)
│   ├── math_helpers.py # Z-scores, vol calculations, EWMA
│   └── state_store.py  # Persistent state (bars, orders, PnL)
│
└── main.py             # Main orchestrator + CLI entry point
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
poetry install
```

### 2. Set Environment Variables

Create a `.env` file in the project root:

```bash
HL_NETWORK=testnet
HL_ADDRESS=0xYourMainWallet
HL_SECRET_KEY=0xYourAPIWalletPrivateKey
HL_VAULT_ADDRESS=0xYourVaultAddress  # Optional: trade on behalf of a vault
```

### 3. Run

**Fast testing (1h intervals, mainnet data, testnet execution):**
```bash
poetry run python -m momentum_trading.main --config config/test_fast.yaml
```

**Dry-run (no orders placed):**
```bash
poetry run python -m momentum_trading.main --config config/test_fast.yaml --dry-run
```

**Production (daily rebalances):**
```bash
poetry run python -m momentum_trading.main --config config/default.yaml
```

**4h swing trading:**
```bash
poetry run python -m momentum_trading.main --config config/4h_swing.yaml
```

## 📋 Configuration Presets

### `config/test_fast.yaml` (Rapid Testing)
- **Interval**: 1h (rebalances every hour)
- **Universe**: Top 5 assets (avoid rate limits)
- **Data Source**: Mainnet (real signals)
- **Execution**: Testnet (safe testing)
- **Order Style**: Market (instant fills)
- **Vol Target**: 50% (aggressive for small test capital)
- **Use Case**: Testing strategy logic with real market data, zero risk

### `config/default.yaml` (Production Daily)
- **Interval**: 1d (rebalances daily at 00:05 UTC)
- **Universe**: Top 40 liquid assets
- **Lookbacks**: 10d, 30d, 90d
- **Order Style**: ALO (maker-first for rebates)
- **Vol Target**: 15% annual
- **Use Case**: Low-churn, cost-efficient momentum harvesting

### `config/4h_swing.yaml` (Higher Frequency)
- **Interval**: 4h (6× daily rebalances)
- **Lookbacks**: 24h, 96h, 240h
- **Tighter Stops**: 2.0× ATR (vs 2.5× for daily)
- **Higher Edge Bar**: 3.0× costs (vs 2.5×)
- **Use Case**: Faster trend capture, more turnover

### `config/mainnet_test.yaml` (Mainnet Testing with Small Capital)
- **Interval**: 4h
- **Universe**: Top 8 assets (concentrated for small capital)
- **Vol Target**: 30% (moderate-aggressive)
- **Per-Asset Cap**: 15% (allows $60 positions on $400)
- **Edge Bar**: 2.5× costs (balanced)
- **Network**: Mainnet (real execution)
- **Use Case**: Testing with $400-1000 capital, positions $30-60 each (above $10 minimum)

## 🎯 Key Parameters Explained

**Universe Selection:**
- `universe.top_n`: How many assets to trade (default 40)
- `universe.min_day_notional`: Minimum $10M daily volume (mainnet) or $0 (testnet)

**Signal Tuning:**
- `signal.lookbacks`: Multiple timeframes to combine (e.g., `[10, 30, 90]` days)
- `funding.lambda_penalty`: How much to penalize expensive funding (1.0 = full penalty)

**Risk Controls:**
- `risk.vol_target_annual`: Portfolio volatility target (15% = moderate, 50% = aggressive testing)
- `risk.w_max`: Max % of capital per asset (5% = diversified, 20% = concentrated testing)
- `trade_gate.min_edge_to_cost_ratio`: Only trade if edge ≥ 2.5× costs

**Execution:**
- `execution.default_style`: "ALO" (maker), "Market" (instant), or "IOC"
- `execution.twap_threshold_usd`: Use TWAP for orders > $50k

## 🔐 Security & Safety

- **API wallet signing**: Your main wallet never touches the exchange; API wallet signs on behalf
- **Vault support**: Trade on behalf of a vault (must be authorized leader)
- **Testnet by default**: Safe testing environment
- **Kill switches**: Auto-halt on daily loss, API errors, or poor edge/friction
- **Idempotent orders**: Deterministic cloIDs prevent duplicate orders on restart
- **Retry/backoff**: Graceful handling of transient failures
- **Rate limiting**: 500ms spacing + caching to avoid 429 errors
- **Dry-run mode**: `--dry-run` flag for zero-risk testing

## 🛠️ Advanced Features

### Mainnet Data + Testnet Execution
Use real market signals with safe testnet execution:
```yaml
hyperliquid:
  network: testnet        # Execute here
  data_network: mainnet   # Pull data from here
```

### Vault Trading
Set `HL_VAULT_ADDRESS` in `.env` and the system trades using vault capital (your wallet just signs).



## 📚 References

- [Spec.md](./Spec.md) - Complete technical specification
- [Strategy.md](./Strategy.md) - Original strategy design and first principles
- [Hyperliquid API Docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api)
- [TSMOM Research (Moskowitz et al)](https://docs.lhpedersen.com/TimeSeriesMomentum.pdf)

## 📝 License

MIT

---

**Status:** 🚧 Under Development - Testnet Only

