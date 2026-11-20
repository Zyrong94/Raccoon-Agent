"""Bind QQ history queries to shared delivery projection."""
from raccoon.interfaces.qq.queries import group_bindings
from raccoon.application.delivery.group_sessions import group_root
from raccoon.application.delivery.group_sessions import list_group_sessions as list_groups, group_session_snapshot as group_snapshot

def list_group_sessions(data_root):
    return list_groups(data_root, bindings=group_bindings)

def group_session_snapshot(data_root, key):
    return group_snapshot(data_root, key, bindings=group_bindings)
