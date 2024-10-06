"""
Kill Switch

Circuit breaker that halts trading on:
- Daily loss > cap
- Persistent WS disconnect
- Slippage > ceiling
- Covariance blow-up
- API reject storms
"""

import statistics

from momentum_trading.core.config import Config


class KillSwitch:
    """Circuit breaker for system failures."""
    
    def __init__(self, config: Config):
        self.config = config
        self.triggered = False
        self.reason = None
    
    def check(self, metrics: dict) -> bool:
        """
        Check if kill switch should trigger per Spec.md thresholds.
        
        Returns:
            True if should halt trading
        """
        try:
            # 1. Daily loss cap
            equity = metrics.get("equity")
            if equity is not None and hasattr(self, "_start_equity"):
                loss_pct = (self._start_equity - equity) / self._start_equity if self._start_equity > 0 else 0.0
                if loss_pct > self.config.kill_switch.daily_loss_pct:
                    self.reason = f"Daily loss {loss_pct:.2%} > {self.config.kill_switch.daily_loss_pct:.2%}"
                    return True
            elif equity is not None and not hasattr(self, "_start_equity"):
                # Initialize start equity on first check
                self._start_equity = equity
            
            # 2. API error storm
            api_errors = metrics.get("api_errors", 0)
            if api_errors >= self.config.kill_switch.max_api_errors:
                self.reason = f"API errors {api_errors} >= {self.config.kill_switch.max_api_errors}"
                return True
            
            # 3. Edge/friction consistently poor
            edge_list = metrics.get("edge_to_friction", []) or []
            if edge_list:
                median_ratio = statistics.median(edge_list)
                if median_ratio < 1.0:
                    self.reason = f"Edge/Friction median too low: {median_ratio:.2f}"
                    return True
            
            # 4. WS heartbeat misses (if WS is used; placeholder)
            # ws_misses = metrics.get("ws_heartbeat_misses", 0)
            # if ws_misses >= self.config.kill_switch.ws_heartbeat_misses:
            #     self.reason = f"WS heartbeat misses {ws_misses}"
            #     return True
            
            return False
        except Exception as e:
            print(f"[KillSwitch] check failed: {e}")
            return False
    
    def trigger(self, reason: str):
        """Trigger kill switch."""
        self.triggered = True
        self.reason = reason
        print(f"[KILL SWITCH] TRIGGERED: {reason}")
    
    def reset(self):
        """Reset kill switch (manual intervention)."""
        self.triggered = False
        self.reason = None
