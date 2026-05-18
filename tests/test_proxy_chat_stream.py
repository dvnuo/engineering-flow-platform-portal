from app.api import proxy


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
