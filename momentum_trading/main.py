"""
Main entry point for momentum trading system.

Orchestrates all components: Scheduler → Data → Signals → Risk → Execution → PnL.
"""

import sys
import os
import argparse
from pathlib import Path
import time
import hashlib
from dotenv import load_dotenv
import pandas as pd
import numpy as np

from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange

from momentum_trading.core.config import Config
from momentum_trading.core.scheduler import Scheduler
from momentum_trading.data.loader import MarketDataLoader
from momentum_trading.signals.engine import SignalEngine
from momentum_trading.risk.engine import RiskEngine
from momentum_trading.execution.router import ExecutionRouter
from momentum_trading.execution.orders import OrderIntent, ExecutionReport
from momentum_trading.monitoring.metrics import MetricsCollector
from momentum_trading.monitoring.kill_switch import KillSwitch
from momentum_trading.utils.state_store import StateStore


class MomentumTradingSystem:
    """
    Main orchestrator for momentum trading system.
    
    Coordinates all components and handles the rebalance loop.
    """
    
    def __init__(self, config: Config, dry_run: bool = False):
        """
        Initialize trading system.
        
        Args:
            config: System configuration
        """
        self.config = config
        
        # Validate config
        errors = config.validate()
        if errors:
            print("[ERROR] Configuration validation failed:")
            for err in errors:
                print(f"  - {err}")
            sys.exit(1)
        
        print(f"[Init] Starting {config.hyperliquid.network} trading system")
        
        # Initialize Hyperliquid clients (with retry for rate limits)
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                self.info = Info(config.hyperliquid.api_url, skip_ws=True)
                
                # Create LocalAccount from private key for Exchange
                wallet = Account.from_key(config.hyperliquid.secret_key)
                vault_address = config.hyperliquid.vault_address if config.hyperliquid.vault_address else None
                self.exchange = Exchange(wallet, config.hyperliquid.api_url, vault_address=vault_address)
                break
            except Exception as e:
                if "429" in str(e) and attempt < max_retries:
                    wait = 5 * attempt
                    print(f"[Init] Rate limited, retrying in {wait}s... (attempt {attempt}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise
        
        # Initialize components (use data_api_url for market data if configured)
        data_config = Config()
        data_config.__dict__.update(config.__dict__)
        if config.hyperliquid.data_api_url != config.hyperliquid.api_url:
            # Override data loader to use data_api_url
            print(f"[Init] Using {config.hyperliquid.data_network or 'configured'} network for data")
            data_config.hyperliquid.api_url = config.hyperliquid.data_api_url
        self.data_loader = MarketDataLoader(data_config)
        self.signal_engine = SignalEngine(data_config, self.data_loader)
        self.risk_engine = RiskEngine(config, self.data_loader)
        self.execution_router = ExecutionRouter(config, self.exchange, self.info, dry_run=dry_run)
        self.metrics = MetricsCollector(config)
        self.kill_switch = KillSwitch(config)
        self.state_store = StateStore()
        
        # Initialize scheduler (pass rebalance callback)
        self.scheduler = Scheduler(config, self.rebalance)
        
        print("[Init] All components initialized")
    
    def rebalance(self):
        """
        Execute one rebalance cycle.
        
        Flow per Spec.md:
        1. Pre-rebalance cleanup (cancel open orders)
        2. Fetch universe & data
        3. Compute signals (SignalEngine)
        4. Compute target weights (RiskEngine)
        5. Execute orders (ExecutionRouter)
        6. Update state & metrics
        """
        try:
            print("\n" + "="*60)
            print("[Rebalance] Starting rebalance cycle")
            print("="*60)
            
            # Check kill switch
            if self.kill_switch.triggered:
                print(f"[Rebalance] HALTED: {self.kill_switch.reason}")
                return
            
            # Step 1: Pre-rebalance cleanup
            print("[Rebalance] Step 1: Canceling open orders...")
            # New cycle id for idempotent cloIDs
            self.execution_router.cycle_id = hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]
            self.execution_router.cancel_all_orders()
            
            # Step 2: Fetch universe & data
            print("[Rebalance] Step 2: Fetching universe...")
            universe = self.data_loader.get_universe()
            print(f"[Rebalance]   Universe: {len(universe)} assets")
            
            # Fetch meta for execution router
            meta, asset_ctxs = self.data_loader.get_meta_and_asset_ctxs()
            self.execution_router.set_asset_map(meta)
            # Build ctx_by_coin mapping
            ctx_by_coin = {
                meta["universe"][i]["name"]: asset_ctxs[i]
                for i in range(len(asset_ctxs))
            }
            
            # Get account state (vault if configured, else personal account)
            query_address = self.config.hyperliquid.vault_address if self.config.hyperliquid.vault_address else self.config.hyperliquid.address
            user_state = self.info.user_state(query_address)
            equity = float(user_state["marginSummary"]["accountValue"])
            account_type = "vault" if self.config.hyperliquid.vault_address else "personal"
            print(f"[Rebalance]   Equity ({account_type}): ${equity:,.2f}")
            # Cache equity for intent conversion later
            self._account_equity_cache = equity
            
            # Current positions
            current_positions = {
                ap["position"]["coin"]: float(ap["position"]["szi"])
                for ap in user_state.get("assetPositions", [])
            }
            
            # Step 3: Compute signals
            print("[Rebalance] Step 3: Computing signals...")
            signals = self.signal_engine.compute_signals(universe)
            print(f"[Rebalance]   Generated {len(signals)} signals")
            
            # Step 4: Compute target weights
            print("[Rebalance] Step 4: Computing target weights...")
            # Build returns DataFrame (fast placeholder: use signal interval closes)
            # In production, persist returns and maintain a rolling matrix.
            now_ms = int(time.time() * 1000)
            interval = self.config.bars.interval
            interval_ms = {"1d": 86_400_000, "4h": 14_400_000, "1h": 3_600_000}.get(interval, 86_400_000)
            bars = 252 if interval == "1d" else (6 * 365 if interval == "4h" else 365)
            end_ms = (now_ms // interval_ms) * interval_ms - 1
            start_ms = end_ms - bars * interval_ms
            rets = {}
            for c in universe:
                try:
                    cs = self.data_loader.get_candles(c, interval, start_ms, end_ms)
                    cs.sort(key=lambda x: x["t"])
                    closes = [k["c"] for k in cs]
                    if len(closes) > 2:
                        r = np.diff(np.log(np.array(closes)))
                        rets[c] = r
                except Exception:
                    continue
            # Align lengths
            if rets:
                min_len = min(len(v) for v in rets.values() if len(v) > 2)
                rets = {k: v[-min_len:] for k, v in rets.items() if len(v) >= min_len}
                returns_df = pd.DataFrame(rets)
            else:
                returns_df = None
            target_weights = self.risk_engine.compute_target_weights(
                signals,
                returns_df,
                equity,
                ctx_by_coin
            )
            print(f"[Rebalance]   Target weights: {len(target_weights)} positions")
            
            # Step 5: Execute orders
            print("[Rebalance] Step 5: Executing orders...")
            intents = self._weights_to_intents(target_weights, current_positions, ctx_by_coin)
            execution_reports = self.execution_router.execute_intents(
                intents,
                current_positions
            )
            print(f"[Rebalance]   Executed {len(execution_reports)} orders")
            
            # Step 6: Update state & metrics
            print("[Rebalance] Step 6: Updating state...")
            
            # Persist equity
            self.state_store.append_jsonl("equity", {"t": time.time(), "equity": equity})
            
            # Persist execution reports (orders placed)
            for rep in execution_reports:
                self.state_store.append_jsonl("orders", {
                    "t": time.time(),
                    "oid": rep.oid,
                    "cloid": rep.cloid,
                    "status": rep.status,
                    "filled_sz": rep.filled_sz,
                    "avg_px": rep.avg_px,
                    "fees": rep.fees,
                })
            
            # Snapshot
            self.state_store.save_snapshot("rebalance", {
                "equity": equity,
                "n_assets": len(universe),
                "n_signals": len(signals),
                "n_trades": len(execution_reports),
            })
            
            # Collect metrics
            edge_ratios = [tw.edge / max(tw.friction, 1e-9) for tw in target_weights.values()]
            for ratio in edge_ratios:
                self.metrics.record_edge_friction(ratio)
            
            metrics = {
                "edge_to_friction": edge_ratios,
                "api_errors": self.execution_router.api_error_count,
                "equity": equity,
            }
            
            # Check kill switch conditions
            if self.kill_switch.check(metrics):
                self.kill_switch.trigger("Metrics threshold breached")
            
            print("[Rebalance] Cycle complete\n")
        
        except Exception as e:
            print(f"[Rebalance] ERROR: {e}")
            import traceback
            traceback.print_exc()
            # Don't crash the scheduler
    
    def _weights_to_intents(self, target_weights, current_positions, ctx_by_coin):
        """Convert target weights to OrderIntents.
        Chooses ALO by default; TWAP if notional > threshold; IOC for reduce-only exits.
        """
        intents = []
        equity = getattr(self, "_account_equity_cache", None)
        if equity is None:
            return intents
        twap_threshold = self.config.execution.twap_threshold_usd
        min_order_value = 10.0  # Hyperliquid minimum order value
        
        print(f"[Intents] Converting {len(target_weights)} weights to intents (equity=${equity:.2f})")
        
        for coin, tw in target_weights.items():
            cur_qty = float(current_positions.get(coin, 0.0))
            mid_px = float(ctx_by_coin.get(coin, {}).get("midPx", 0.0))
            if mid_px <= 0:
                print(f"[Intents] Skipping {coin}: no mid price")
                continue
            target_notional = tw.w_scaled * equity
            current_notional = cur_qty * mid_px
            delta = target_notional - current_notional
            
            print(f"[Intents] {coin}: target=${target_notional:.2f}, current=${current_notional:.2f}, delta=${delta:.2f}")
            
            # Skip if delta is too small (below exchange minimum)
            if abs(delta) < min_order_value:
                print(f"[Intents] Skipping {coin}: delta ${abs(delta):.2f} < ${min_order_value:.2f} minimum")
                continue
            side = "Buy" if delta > 0 else "Sell"
            # Choose style: TWAP for large, else config default (ALO/IOC/Market)
            if abs(delta) > twap_threshold and self.config.execution.twap_enabled:
                style = "TWAP"
            else:
                style = self.config.execution.default_style
            # Reduce-only if delta reduces existing exposure without flipping
            reduce_only = False
            if cur_qty != 0:
                cur_dir = 1 if cur_qty > 0 else -1
                new_dir = 1 if delta > 0 else -1
                if cur_dir == new_dir:
                    # Same direction: not reduce-only
                    reduce_only = False
                else:
                    # Opposite direction: if magnitude smaller than current exposure, it's a partial close
                    if abs(delta) < abs(current_notional):
                        reduce_only = True
                    else:
                        reduce_only = False  # flip will be handled by router
            intents.append(
                OrderIntent(
                    coin=coin,
                    side=side,
                    abs_notional=abs(delta),
                    urgency="Normal",
                    style=style,
                    reduce_only=reduce_only,
                )
            )
        return intents
    
    def run(self):
        """Run the trading system (blocks indefinitely)."""
        # Should this be there for prod?
        print("[Main] Running initial rebalance...")
        try:
            self.rebalance()
        except Exception as e:
            print(f"[Main] Initial rebalance failed: {e}")
            import traceback
            traceback.print_exc()
        
        print("[Main] Starting scheduler...")
        try:
            self.scheduler.run_forever()
        except KeyboardInterrupt:
            print("\n[Main] Shutdown signal received")
            self.scheduler.stop()
            print("[Main] Goodbye!")


def main():
    """CLI entry point."""
    print("="*60)
    print("  MOMENTUM TRADING SYSTEM - Hyperliquid")
    print("="*60)
    
    # CLI args
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="", help="Path to YAML config file")
    parser.add_argument("--dry-run", action="store_true", help="Run without placing/cancelling any orders")
    args = parser.parse_args()
    
    # Load .env if present (before Config) to populate HL_* variables
    # Load .env if present
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    
    # Load config (defaults + env overrides or YAML)
    if args.config:
        config = Config.from_yaml(args.config)
    else:
        config = Config()
    
    # Initialize and run system
    system = MomentumTradingSystem(config, dry_run=args.dry_run)
    system.run()


if __name__ == "__main__":
    main()

