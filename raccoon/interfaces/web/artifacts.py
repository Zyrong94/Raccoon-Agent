"""HTTP artifact addresses and scoped resource projections."""
from urllib.parse import quote

def scoped_artifacts(artifacts, project_id, session_id):
    """Path scope survives relative CSS/JS/asset requests made by Canvas HTML."""
    prefix = f'/api/projects/{project_id}/sessions/{session_id}/artifacts'
    descriptors = []
    for identity, item in artifacts.items():
        descriptor = {key: value for key, value in item.items() if key not in {'path', 'modified_ns'}}
        base = f'{prefix}/{identity}'
        version = f"{item['modified_ns']}-{item['size']}"
        descriptor['content_url'] = f'{base}/content?v={version}'
        descriptor['download_url'] = f'{base}/download'
        if item['suffix'] in {'.html', '.htm'}:
            name = quote(item['name'])
            descriptor['canvas_url'] = f'{base}/canvas/{name}?v={version}'
        descriptors.append(descriptor)
    return descriptors
