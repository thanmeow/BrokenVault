import io

from client.progress import MIB, Progress


class FakeTty(io.StringIO):
    def isatty(self):
        return True


def test_redirected_output_prints_whole_lines():
    out = io.StringIO()
    p = Progress("uploading", 3, 3 * MIB, out)
    for _ in range(3):
        p.advance(MIB)
    p.done()
    lines = out.getvalue().splitlines()
    assert lines[0] == "uploading: 1/3 chunks, 1.0/3.0 MiB"
    assert lines[-1] == "uploading: 3/3 chunks, 3.0/3.0 MiB"
    assert "\r" not in out.getvalue()


def test_terminal_updates_in_place_and_ends_with_newline():
    out = FakeTty()
    p = Progress("restoring", 2, 2 * MIB, out)
    p.interval = 0  # show every update
    p.advance(MIB)
    p.advance(MIB)
    p.done()
    text = out.getvalue()
    assert text == "\rrestoring: 1/2 chunks, 1.0/2.0 MiB\rrestoring: 2/2 chunks, 2.0/2.0 MiB\n"


def test_final_count_shown_even_if_throttled():
    out = io.StringIO()
    p = Progress("uploading", 100, 100, out)
    for _ in range(100):
        p.advance(1)
    p.done()
    assert out.getvalue().splitlines()[-1].startswith("uploading: 100/100 chunks")


def test_silent_without_stream_or_work():
    Progress("x", 5, 5, None).done()  # no stream: nothing to do, no error
    out = io.StringIO()
    Progress("uploading", 0, 0, out).done()
    assert out.getvalue() == ""
