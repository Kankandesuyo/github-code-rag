def source_link(repository_url, commit_sha, path):
    """Build a GitHub source permalink pinned to the imported commit SHA."""
    return f"{repository_url}/blob/{commit_sha}/{path}"
