def reciprocal_rank_fusion(rankings):
    """Merge vector and lexical search results by reciprocal rank."""
    scores = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, 1):
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return scores
