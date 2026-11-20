"""pytest 公共配置：将测试配置与用户配置隔离。"""

import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_config_home():
    """把全局配置根指向临时目录，避免测试污染真实配置。

    没有这个隔离时，每个构造 runtime 的测试都会把临时项目登记进真实的
    ``~/.config/raccoon/managed/registry.json``。登记条目会一直累积，而每次读取
    配置、每次构造 runtime 都要为每条登记付出文件系统检查，导致真实应用的
    启动与切换随着测试次数增加而变慢。
    """

    with tempfile.TemporaryDirectory(prefix="raccoon-test-config-") as home:
        previous_home = os.environ.get("HOME")
        os.environ["HOME"] = home
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = home
        try:
            yield home
        finally:
            if previous_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous_home
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous
