import pytest

from common.paths import PathError, check_rel_path, safe_join


@pytest.mark.parametrize("path", ["a", "a/b", "dir/sub/file.txt", "..hidden", "a/.b", "a..b"])
def test_accepts_normal_relative_paths(path):
    assert check_rel_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "/",
        "C:/Windows",
        "c:file",
        "a\\b",
        "..\\evil",
        "..",
        "../x",
        "a/../../x",
        "a/..",
        ".",
        "./a",
        "a/./b",
        "a//b",
        "a/",
        "a\x00b",
        None,
        123,
    ],
)
def test_rejects_unsafe_paths(path):
    with pytest.raises(PathError):
        check_rel_path(path)


def test_safe_join_stays_inside_root(tmp_path):
    assert safe_join(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


@pytest.mark.parametrize("path", ["../x", "/abs", "a/../../x"])
def test_safe_join_rejects_escapes(tmp_path, path):
    with pytest.raises(PathError):
        safe_join(tmp_path, path)
