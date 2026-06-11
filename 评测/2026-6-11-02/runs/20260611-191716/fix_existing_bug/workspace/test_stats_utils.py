import pytest

from stats_utils import average


def test_average_values():
    assert average([2, 4, 6]) == 4


def test_average_single_value():
    assert average([10]) == 10


def test_average_empty_list():
    with pytest.raises(ValueError):
        average([])
