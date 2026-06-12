from string_utils import slugify


def test_slugify_basic_words():
    assert slugify("Hello World") == "hello-world"


def test_slugify_punctuation_and_spaces():
    assert slugify("  Agent Loop: Demo!!  ") == "agent-loop-demo"


def test_slugify_collapses_runs():
    assert slugify("a---b___c") == "a-b-c"


def test_slugify_empty_result():
    assert slugify("!!!") == ""
