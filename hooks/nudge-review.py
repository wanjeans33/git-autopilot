#!/usr/bin/env python3
"""旧名字，只做转发。hook 配置在会话启动时快照，装过旧版的会话还会调这个路径；重新 ./install.sh 后就不再引用它。"""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "wrap-up.py"), run_name="__main__")
