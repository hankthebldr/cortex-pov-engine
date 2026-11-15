"""Re-export: the token bucket lives in the tenant quota ledger (one budget)."""
from integrations.xsiam.ledger import TokenBucket

__all__ = ["TokenBucket"]
