from calc import add, mul


def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_mul():
    assert mul(2, 3) == 6
    assert mul(-1, 5) == -5
    assert mul(0, 100) == 0
