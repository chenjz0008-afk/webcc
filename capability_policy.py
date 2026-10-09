"""Explicit boundaries for the web-account Messages transport."""
import re


def capabilities():
    return {'type': 'capabilities', 'upstream': 'claude_web', 'model_policy': 'upstream_default',
        'adapters': {'standard-tools-v1': 'automatic_validated_client_tools', 'prompt-v1': 'validated_client_tools', 'citations-v1': 'verified_document_locations',
                     'mcp-v1': 'remote_mcp_and_checkpoints', 'e2b-v1': 'isolated_code_execution', 'webcc-native-v1': 'native_stream_and_verified_sources'},
        'supported': ['text', 'images', 'text_pdf', 'files', 'batches', 'skills', 'web_search', 'web_fetch'],
        'unverified': ['native_thinking_signature', 'native_strict_sampling', 'prompt_caching'],
        'request_extensions': 'passthrough; acceptance does not prove upstream execution',
        'exact_model': 'unavailable',
        'deferred': ['strict_search_domains', 'strict_search_count'],
        'search_results': 'actual_web_events; platform_verified_quotes_and_owner_bound_source_state',
        'thinking_state': 'native_signature_passthrough; webccsig_v1_fallback; ttl_86400',
        'processing_cache': 'tenant_isolated_encrypted_preprocessing; not_model_kv_cache',
        'streaming': 'incremental_text_thinking_and_client_tool_parameters; verified_completion'}


def model_name(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', value) else 'unknown'
