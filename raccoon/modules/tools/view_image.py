"""Local image viewing, following DeerFlow's format and byte-limit contract."""

from pathlib import Path

from raccoon.modules.tools.path_permissions import with_read_permission
from raccoon.modules.tools.types import MAX_TOOL_IMAGE_BYTES, Tool, ToolImage, ToolResult, make_error_result
from raccoon.modules.tools.introspection import tool_from_function
from raccoon.runtime.sandbox import PathSandbox
from raccoon.modules.permissions.sandbox_access import SandboxAccess

IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
               '.webp': 'image/webp', '.gif': 'image/gif'}


def create_view_image_tool(root: str | Path, *, access: SandboxAccess | None = None) -> Tool:
    sandbox = PathSandbox(root, access=access)

    def view_image(image_path: str) -> ToolResult:
        """查看一张本地图片并提供给视觉模型；支持 PNG、JPEG、WebP、GIF，单张最多 20 MiB。"""
        # Attachments also imports tool types during session initialization.
        from raccoon.modules.sessions.user_attachments import validate_tool_image

        try:
            target = sandbox.resolve_validated(image_path, expect='file')
            media_type = IMAGE_TYPES.get(target.suffix.lower())
            if media_type is None:
                raise ValueError('仅支持 PNG、JPEG、WebP、GIF 图片')
            with target.open('rb') as stream:
                raw = stream.read(MAX_TOOL_IMAGE_BYTES + 1)
            if len(raw) > MAX_TOOL_IMAGE_BYTES:
                raise ValueError('图片超过 20 MiB 上限')
            validate_tool_image(raw, media_type)
        except (OSError, ValueError) as error:
            return make_error_result('view_image', f'无法查看图片：{error}')
        source = sandbox.relative(target)
        return ToolResult(name='view_image', ok=True, content=f'图片：{source}',
                          data={'path': source, 'media_type': media_type},
                          images=[ToolImage(data=raw, media_type=media_type, source_path=source)])

    return with_read_permission(tool_from_function(view_image, exposure="deferred"), target_builder=lambda args: str(args.get('image_path') or '.'),
                                reason='查看图片需要文件读取权限。')
