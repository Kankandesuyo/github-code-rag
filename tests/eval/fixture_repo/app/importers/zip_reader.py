def check_zip_entry(member, limit):
    """Reject archive entries larger than the decompression size limit."""
    if member.file_size > limit:
        raise ValueError("archive size limit exceeded")
