def delete_repository(catalog, repository_id):
    """Remove a saved repository from the catalog and clear its index."""
    return catalog.delete(repository_id)
