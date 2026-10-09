"""PDF and text document conversion for ClewdR web inputs."""
import base64
import binascii


class DocumentProblem(ValueError):
    pass


def prepare(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
        return False
    changes = []
    for message in payload['messages']:
        if not isinstance(message, dict) or not isinstance(message.get('content'), list):
            continue
        blocks = []
        for block in message['content']:
            if not isinstance(block, dict) or block.get('type') != 'document':
                blocks.append(block); continue
            if message.get('role') != 'user' or set(block) - {'type', 'source', 'title', 'context', 'citations'}:
                raise DocumentProblem('网页 PDF 仅支持 user 内联文档；缓存等扩展尚不支持')
            citations = block.get('citations', {})
            if not isinstance(citations, dict) or set(citations) - {'enabled'} or type(citations.get('enabled', False)) is not bool:
                raise DocumentProblem('citations 配置无效')
            if citations.get('enabled'):
                raise DocumentProblem('网页通道不提供原生 citations；请关闭该配置')
            source = block.get('source')
            if isinstance(source, dict) and source.get('type') == 'text':
                if set(source) - {'type', 'media_type', 'data'} or source.get('media_type', 'text/plain') != 'text/plain' or not isinstance(source.get('data'), str):
                    raise DocumentProblem('文本 document 需要 text/plain 字符串来源')
                metadata = []
                for field in ('title', 'context'):
                    if field in block:
                        if not isinstance(block[field], str):
                            raise DocumentProblem('文档 title/context 必须是文本')
                        metadata.append(f'Document {field}: {block[field]}')
                blocks.append({'type': 'text', 'text': '\n'.join(metadata + [source['data']])})
                continue
            if not isinstance(source, dict) or set(source) != {'type', 'media_type', 'data'} or source.get('type') != 'base64' or source.get('media_type') != 'application/pdf':
                raise DocumentProblem('网页 PDF 仅支持 application/pdf 的 base64 来源；URL 和 file_id 尚不支持')
            encoded = source['data']
            if not isinstance(encoded, str) or len(encoded) > 28 * 1024 * 1024:
                raise DocumentProblem('PDF 编码无效或超过大小限制')
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise DocumentProblem('PDF base64 编码无效') from None
            if len(raw) > 20 * 1024 * 1024 or not raw.startswith(b'%PDF-'):
                raise DocumentProblem('PDF 超过 20 MiB 或缺少有效 PDF 文件头')
            del raw
            metadata = []
            for field in ('title', 'context'):
                if field in block:
                    if not isinstance(block[field], str):
                        raise DocumentProblem('PDF title/context 必须是文本')
                    metadata.append(f'Document {field}: {block[field]}')
            if metadata:
                blocks.append({'type': 'text', 'text': '\n'.join(metadata)})
            blocks.append({'type': 'image', 'source': dict(source)})
        if blocks != message['content']:
            changes.append((message, blocks))
    for message, blocks in changes:
        message['content'] = blocks
    return bool(changes)
