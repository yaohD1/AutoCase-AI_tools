"""录制存档：把还没录完的步骤落库，下次接着录。

与 records.py 分开：那边是「已经写进脚本文件」的生成记录（带 sha / commit），
这里只是步骤快照，没有文件可写。

存的是 `snapshot()` 里的公开步骤——已经剥掉 `_frame` / `_doc` 这类页面对象，
保留 `target.snap`（role / name / testid / css 等定位信号）。恢复时原样灌回会话，
生成任务书和调试回放都只吃这些纯数据，不依赖内存里的页面引用。
"""
import json
from datetime import datetime

from app.models import db
from app.models.recording_draft import RecordingDraft

MAX_TITLE = 200
# 一份草稿最多存这么多步：再多就不是「没录完」，而是该直接生成脚本了；
# 也避免一次误操作把几百兆的截图塞进 sqlite。
MAX_STEPS = 800


def _lean_target(target):
    """去掉只服务于「录制当场」的大字段。

    shot 是按下那一刻的截图（base64），matches 是候选元素清单——生成、回放、
    续录都用不到，留着只会让草稿膨胀几十倍。
    """
    if not isinstance(target, dict):
        return target
    out = dict(target)
    out.pop('shot', None)
    out.pop('matches', None)
    return out


def _lean_steps(steps):
    out = []
    for step in (steps or [])[:MAX_STEPS]:
        item = dict(step)
        if 'target' in item:
            item['target'] = _lean_target(item['target'])
        out.append(item)
    return out


def _clean(value, limit=None):
    text = str(value or '').strip()
    return text[:limit] if limit else text


def save(session, config=None, title=None):
    """把当前会话存成一条草稿；同一条会话再次保存时覆盖原来那条。"""
    config = config or {}
    snapshot = session.snapshot()
    # 存完整 IR 而不是 snapshot['steps']：后者是给前端渲染的视图，会把
    # target.candidates 的定位 spec 剥掉，续录后生成任务书 / 回放就拿不到真定位
    # （RF 会直接 KeyError('spec')）。ir() 只是去掉 _frame 这类页面对象，不碰 spec。
    steps = _lean_steps(session.ir())
    draft = None
    draft_id = getattr(session, '_draft_id', None)
    if draft_id:
        draft = db.session.get(RecordingDraft, draft_id)
    if draft is None:
        draft = RecordingDraft()
        db.session.add(draft)

    draft.title = _clean(title or config.get('title'), MAX_TITLE) or None
    # base_url 以会话为准：前端配置可能在录制开始后被改过，那个值并不对应这个浏览器。
    draft.base_url = session.base_url
    draft.workspace = _clean(config.get('workspace') or getattr(session, 'workspace', None), 1000)
    draft.framework = _clean(config.get('framework') or getattr(session, 'framework', None), 50)
    draft.target_path = _clean(config.get('target_path'), 500)
    draft.model = _clean(config.get('model'), 200)
    draft.python = _clean(config.get('python'), 1000)
    draft.step_count = len(steps)
    draft.steps_json = json.dumps(steps, ensure_ascii=False)
    # 后置处理跟步骤一起走。存的是「动作定义 + 实参」而不是一个 id：
    # 动作库是项目级的，换了工作区这条引用就指向空气，存档里必须自带一份。
    read_teardown = getattr(session, 'teardown_definition', None)
    teardown = read_teardown() if callable(read_teardown) else None
    draft.teardown_json = json.dumps(teardown, ensure_ascii=False) if teardown else None
    # 可引用元素（拾取入库的）跟着走：续录后断言还能引用到它们。同样剥掉大字段。
    picks = snapshot.get('picks') or []
    draft.picks_json = json.dumps(
        [dict(p, target=_lean_target(p.get('target'))) for p in picks][:MAX_STEPS],
        ensure_ascii=False)
    db.session.commit()

    # 记住这条：本次录制之后再点「保存步骤」就是更新它，不在列表里堆副本。
    session._draft_id = draft.id
    return draft


def list_all(limit=50):
    return RecordingDraft.query.order_by(RecordingDraft.updated_at.desc()).limit(limit).all()


def get(draft_id):
    return db.session.get(RecordingDraft, draft_id)


def delete(draft):
    db.session.delete(draft)
    db.session.commit()


def steps_of(draft):
    return draft.steps()


def teardown_of(draft):
    """存档里的后置处理定义；没有就是 None。"""
    return draft.teardown()

def touch(draft):
    """续录时把「最近使用」顶上去，列表按它排序。"""
    draft.updated_at = datetime.utcnow()
    db.session.commit()
    return draft
