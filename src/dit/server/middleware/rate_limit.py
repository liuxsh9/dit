from slowapi import Limiter
from slowapi.util import get_remote_address


def create_limiter(rate_limit: str) -> Limiter:
    return Limiter(
        key_func=get_remote_address,
        default_limits=[rate_limit] if rate_limit else [],
    )
