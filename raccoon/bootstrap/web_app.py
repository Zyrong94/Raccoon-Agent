"""Compose the local Web API with execution, workspace and delivery owners."""
from pathlib import Path

from raccoon.bootstrap.runtime import create_runtime
from raccoon.interfaces.web import conversation_actions as actions
from raccoon.bootstrap.main_host import MainSessionBridge
from raccoon.app.web import WebChatApplication
from raccoon.interfaces.web.server import create_web_app as create_http_app
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.sessions.identity import new_session_id
from raccoon.modules.sessions.thread_data import default_thread_data


def create_web_app(runtime=None, *, runtime_factory=create_runtime, initial_projectless=False,
                   project_path=None, data_root=None, session_id=None, app_config=None, model_ref=None):
    if runtime is not None:
        project_path, data_root = runtime.project_path, runtime.catalog.root
        session_id, app_config = runtime.session.session_id, runtime.app_config
    else:
        project_path = Path(project_path).resolve()
        data_root = Path(data_root).resolve() if data_root is not None else project_path / '.raccoon'
        if session_id is None:
            session_id = new_session_id()
            SessionStore(data_root).create(session_id=session_id, thread_data=default_thread_data(
                store_root=data_root, session_id=session_id, project_root=project_path,
            ))
    workbench = WebChatApplication(
        project_path=project_path, data_root=data_root, session_id=session_id, app_config=app_config,
        model_ref=model_ref, runtime=runtime, runtime_factory=runtime_factory,
    )
    main_bridge = MainSessionBridge(project_path, data_root, runtime_factory=runtime_factory)
    from raccoon.bootstrap.scheduling import SchedulingService
    scheduling = SchedulingService(data_root, runtime_factory=runtime_factory,
                                   main_bridge=main_bridge, workbench=workbench)
    workbench.scheduling = scheduling
    for display in workbench.displays.values():
        actions.install_scheduling_tools(display)
    return create_http_app(data_root, workbench=workbench, main_bridge=main_bridge,
                           scheduling=scheduling, initial_projectless=initial_projectless)
