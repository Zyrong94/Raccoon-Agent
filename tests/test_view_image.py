from io import BytesIO

import pytest
from PIL import Image

from raccoon.modules.tools.registry import ToolRegistry
from raccoon.modules.tools.builtin import create_builtin_tools
from raccoon.modules.tools.execution import execute_tool


@pytest.mark.parametrize('fmt,suffix,mime', [('PNG','png','image/png'), ('JPEG','jpg','image/jpeg'),
                                           ('WEBP','webp','image/webp'), ('GIF','gif','image/gif')])
def test_view_image_roundtrips_original_image(tmp_path, fmt, suffix, mime):
    stream = BytesIO()
    Image.new('RGB', (16, 12), 'red').save(stream, format=fmt)
    path = tmp_path / f'image.{suffix}'
    path.write_bytes(stream.getvalue())
    result = execute_tool(ToolRegistry(create_builtin_tools(tmp_path)), 'view_image', {'image_path': path.name})
    assert result.ok
    assert result.images[0].data == path.read_bytes()
    assert result.images[0].media_type == mime
    assert result.images[0].page is None


def test_view_image_rejects_mismatch_missing_and_outside_path(tmp_path):
    stream = BytesIO()
    Image.new('RGB', (2, 2)).save(stream, format='PNG')
    (tmp_path / 'fake.jpg').write_bytes(stream.getvalue())
    tools = create_builtin_tools(tmp_path)
    registry = ToolRegistry(tools)
    for path in ['fake.jpg', 'missing.png', '../outside.png']:
        assert not execute_tool(registry, 'view_image', {'image_path': path}).ok


def test_view_image_permission_uses_actual_image_parameter(tmp_path):
    from raccoon.modules.tools.view_image import create_view_image_tool
    tool = create_view_image_tool(tmp_path)
    assert tool.permission.target_builder({'image_path': 'private.png'}) == 'private.png'


def test_view_image_rejects_oversized_file_before_decode(tmp_path):
    from raccoon.modules.tools.types import MAX_TOOL_IMAGE_BYTES
    path = tmp_path / 'huge.png'
    with path.open('wb') as stream:
        stream.truncate(MAX_TOOL_IMAGE_BYTES + 1)
    result = execute_tool(ToolRegistry(create_builtin_tools(tmp_path)), 'view_image', {'image_path': path.name})
    assert not result.ok
    assert '20 MiB' in result.error
