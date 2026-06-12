"""Tree session：path、持久化 round-trip、branch、branch summary。"""
from __future__ import annotations

from mu.session import Session


def test_append_builds_linear_path(tmp_path):
    s = Session(base_dir=tmp_path)
    s.append({"role": "system", "content": "sys"})
    s.append({"role": "user", "content": "hi"})
    path = s.path_to_head()
    assert [m["role"] for m in path] == ["system", "user"]


def test_persistence_round_trip(tmp_path):
    s = Session(base_dir=tmp_path)
    s.append({"role": "system", "content": "sys"})
    s.append({"role": "user", "content": "hi"})
    s.append({"role": "assistant", "content": "yo"})
    assert s.path.exists()

    loaded = Session.load(s.id, base_dir=tmp_path)
    assert loaded.path_to_head() == s.path_to_head()
    assert loaded.head == s.head


def test_branch_from_forks_history(tmp_path):
    s = Session(base_dir=tmp_path)
    s.append({"role": "system", "content": "sys"})
    user_id = s.append({"role": "user", "content": "main task"})
    s.append({"role": "assistant", "content": "main answer"})
    # 从 user 节点分支，走另一条路径
    s.branch_from(user_id)
    s.append({"role": "assistant", "content": "side answer"})

    path = s.path_to_head()
    assert [m["content"] for m in path] == ["sys", "main task", "side answer"]
    # 两个叶子（main answer、side answer）
    assert len(s.leaves()) == 2


def test_branch_summary_message(tmp_path):
    s = Session(base_dir=tmp_path)
    s.append({"role": "user", "content": "main"})
    s.add_branch_summary("侧分支里修好了工具 X")
    path = s.path_to_head()
    assert path[-1]["type"] == "branch_summary"
    assert "工具 X" in path[-1]["content"]


def test_branch_from_unknown_node_raises(tmp_path):
    s = Session(base_dir=tmp_path)
    s.append({"role": "user", "content": "x"})
    try:
        s.branch_from("does-not-exist")
        assert False, "should have raised"
    except KeyError:
        pass
