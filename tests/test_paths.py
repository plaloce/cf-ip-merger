from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from cf_merger import paths


class WorkingDirectoryTests(unittest.TestCase):
    """工作目录隔离：第三方组件（如显卡驱动）往 cwd 写日志时的兜底。"""

    def setUp(self) -> None:
        self.original = Path.cwd()

    def tearDown(self) -> None:
        os.chdir(self.original)

    def test_runtime_dir_lives_under_system_temp(self) -> None:
        runtime = paths.get_runtime_dir()
        if runtime is None:
            self.skipTest("当前环境缺少系统临时目录变量")
        self.assertEqual(runtime.name, "run")
        self.assertEqual(runtime.parent.name, paths.RUNTIME_DIRNAME)

    def test_isolate_working_directory_switches_to_runtime_dir(self) -> None:
        target = paths.isolate_working_directory()
        if target is None:
            self.skipTest("当前环境缺少系统临时目录变量")
        self.assertTrue(target.is_dir())
        self.assertEqual(Path.cwd().resolve(), target.resolve())

    @mock.patch.dict(os.environ, {"LOCALAPPDATA": "", "TEMP": "", "TMP": ""})
    def test_no_temp_env_keeps_working_directory(self) -> None:
        self.assertIsNone(paths.get_runtime_dir())
        self.assertIsNone(paths.isolate_working_directory())
        self.assertEqual(Path.cwd().resolve(), self.original.resolve())

    def test_app_dir_is_absolute_and_existing(self) -> None:
        app_dir = paths.get_app_dir()
        self.assertTrue(app_dir.is_absolute())
        self.assertTrue(app_dir.is_dir())

    def test_app_dir_independent_from_cwd(self) -> None:
        before = paths.get_app_dir()
        paths.isolate_working_directory()
        self.assertEqual(paths.get_app_dir(), before)


if __name__ == "__main__":
    unittest.main()
