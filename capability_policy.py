"""Explicit boundaries for the web-account Messages transport."""
import re


def capabilities():
    return {'type': 'capabilities', 'upstream': 'claude_web', 'model_policy': 'upstream_default',
        'adapters': {'standard-tools-v1': 'automatic_validated_client_tools', 'prompt-v1': 'validated_client_tools', 'citations-v1': 'verified_document_locations',
                     'mcp-v1': 'remote_mcp_and_checkpoints', 'e2b-v1': 'isolated_code_execution'},
        'supported': ['text', 'images', 'text_pdf', 'files', 'batches', 'skills', 'web_search', 'web_fetch'],
        'unverified': ['native_thinking_signature', 'native_strict_sampling', 'prompt_caching'],
        'request_extensions': 'passthrough; acceptance does not prove upstream execution',
        'exact_model': 'unavailable',
        'deferred': ['strict_search_domains', 'strict_search_count'],
        'search_results': 'web_events_and_source_metadata; no_native_encrypted_provenance'}


def model_name(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', value) else 'unknown'
