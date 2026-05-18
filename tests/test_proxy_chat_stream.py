from app.api import proxy


def _proxy_source() -> str:
    from pathlib import Path

    return Path("app/api/proxy.py").read_text(encoding="utf-8")


def test_stream_chat_paths_are_classified_correctly():
    assert proxy._is_direct_chat_execution_path('POST', 'api/chat/stream') is True
    assert proxy._is_direct_chat_execution_path('POST', '/api/chat/stream') is True
    assert proxy._is_streaming_runtime_path('POST', 'api/chat/stream') is True
    assert proxy._is_streaming_runtime_path('POST', '/api/chat/stream') is True


def test_streaming_headers_whitelist():
    selected = proxy._select_streaming_response_headers({
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
        'x-accel-buffering': 'no',
        'x-extra': 'ignored',
    })
    assert selected['content-type'] == 'text/event-stream'
    assert selected['cache-control'] == 'no-cache'
    assert selected['x-accel-buffering'] == 'no'
    assert 'x-extra' not in selected


def test_chat_stream_proxy_does_not_buffer():
    source = _proxy_source()
    streaming_start = source.index("if _is_streaming_runtime_path(request.method, subpath):")
    streaming_end = source.index("filtered_query_items = _filter_proxy_query_items", streaming_start)
    streaming_block = source[streaming_start:streaming_end]

    assert "httpx.AsyncClient(timeout=None)" in streaming_block
    assert "client.stream(" in streaming_block
    assert "async for chunk in upstream_response.aiter_raw():" in streaming_block
    assert "yield chunk" in streaming_block
    assert "StreamingResponse(" in streaming_block
    assert ".content" not in streaming_block
    assert "api/tasks" not in streaming_block


def test_internal_runtime_paths_stay_control_plane_only():
    assert proxy._is_control_plane_only_runtime_path('api/internal/sessions') is True
    assert proxy._is_control_plane_only_runtime_path('api/internal/anything') is True


def test_chat_payload_enrichment_applies_metadata_and_model_override():
    payload = {'message': 'hi', 'model_override': 'gpt-4.1', 'request_id': 'req_123'}
    metadata = {'provider': 'openai'}
    class _User: pass
    enriched = proxy._enrich_chat_payload_with_runtime_metadata(payload, metadata, _User(), runtime_type='opencode')
    assert enriched['metadata']['provider'] == 'openai'
    assert 'model' in enriched['metadata']
    assert enriched['request_id'] == 'req_123'
