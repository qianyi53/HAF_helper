# -*- coding: utf-8 -*-
"""HAF-agent 核心包：核安全局文函 筛选(L2)/解析(L3)/建库(L4) 模块。

模块规划（见 docs/02-架构设计.md）：
  classifier.py  L2 筛选打标
  pdf_fetch.py   L3 附件下载
  pdf_parser.py  L3 PDF 解析
  db_builder.py  L4 workbuddy database 导出
  pipeline.py    全流程编排
"""
