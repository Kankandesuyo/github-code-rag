def normalize_queries(question, rewrites):
    """Deduplicate query rewrites and preserve the original question."""
    return list(dict.fromkeys([question, *rewrites]))
