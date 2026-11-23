def verify_session_cookie(cookie, signer):
    """Validate signed session cookies and reject expired login sessions."""
    return signer.verify(cookie, max_age=28800)
