def check_request_rate(ip_address, token_bucket):
    """Apply rate limits using a per-client token bucket."""
    return token_bucket.consume(ip_address)
