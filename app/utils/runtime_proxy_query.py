from fastapi import Request


def _filter_runtime_file_upload_query_items(request: Request) -> list[tuple[str, str]]:
    """Keep runtime upload proxy query forwarding narrowly scoped to explicit allowlisted keys."""
    allowlisted_query = {"session_id"}
    return [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key in allowlisted_query
    ]
