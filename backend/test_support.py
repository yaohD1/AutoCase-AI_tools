# -*- coding: utf-8 -*-
"""测试共用的小工具。

这里只放「防止测试打到真实数据」的闸门：2026-11-24 多个测试文件一起跑时，
DATABASE_URL 在 app.config 里没生效（Config 类属性在 import 时就固化了），
一次 db.drop_all() 把 storage/app.db 清空了，事后只能从数据页里抢救。
"""
from flask import current_app

from app.models.database import db


def test_database_uri():
    return current_app.config.get('SQLALCHEMY_DATABASE_URI') or ''


def reset_database():
    """drop_all + create_all，只允许在临时库上执行。

    判据用 test.db（所有测试的临时库都叫这个名字）。真实库是 storage/app.db，
    命中就直接抛错，宁可让这条测试红，也不能再清一次用户数据。
    """
    uri = test_database_uri()
    if 'test.db' not in uri:
        raise RuntimeError(
            '拒绝清空非测试数据库：%s\n'
            '测试必须在 import app 之前设置 DATABASE_URL 指向临时目录。' % uri)
    db.drop_all()
    db.create_all()
