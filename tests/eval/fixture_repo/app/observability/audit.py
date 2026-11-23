def log_audit_event(sink, action, result):
    """Append security audit events to an audit log for compliance."""
    sink.write({"action": action, "status": result})
