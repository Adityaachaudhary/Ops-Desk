import pytest

from app.compliance.matcher import compare_names


@pytest.mark.parametrize("a,b", [
    ("Rahul Kumar", "RAHUL KUMAR"), ("Rahul Kumar", "Kumar Rahul"), ("Rahul  Kumar", "Rahul K."), ("Mohammad Aslam", "Mohammed Aslam"),
    ("Sunita Devi", "SUNITADEVI"), ("Anil Kumar", "Anil Kumar Singh"), ("Mr. Anil Kumar", "ANIL KUMAR"), ("Shrinivasan Iyer", "Shrinivasan Iyerr"),
])
def test_trivial_variations_pass(a, b):
    assert compare_names(a, b).ok, (a, b)


@pytest.mark.parametrize("a,b", [
    ("Rahul Kumar", "Amit Kumar"), ("Dinesh Prasad", "Devendra Prasad"), ("Rekha Sharma", "Renu Sharma"), ("Ramesh Yadav", "Rakesh Yadav"),
    ("Anil", "Anil Kumar"), ("Anil Kumar", "Anil Kumar Singh Yadav"), ("", "Anil"),
])
def test_different_people_fail(a, b):
    assert not compare_names(a, b).ok, (a, b)
