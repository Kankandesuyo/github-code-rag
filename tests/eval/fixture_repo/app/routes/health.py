def health_probe():
    """Liveness and readiness endpoint for service health checks."""
    return {"status": "ready"}
