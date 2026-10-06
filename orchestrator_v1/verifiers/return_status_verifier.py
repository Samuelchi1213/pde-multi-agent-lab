import sys
from pathlib import Path


def main():
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace))

    try:
        from return_status import determine_return_status
    except Exception as exc:
        print("无法导入 return_status:", exc)
        return 1

    cases = [
        ("2026-10-01 18:00", "2026-10-01 17:59", "正常"),
        ("2026-10-01 18:00", "2026-10-01 18:00", "正常"),
        ("2026-10-01 18:00", "2026-10-01 18:01", "晚返"),
        ("2026-10-01 18:00", "2026-10-02 18:00", "晚返"),
        ("2026-10-01 18:00", None, "未返校"),
        ("2026-10-01 18:00", "", "未返校"),
    ]

    for expected, attendance, wanted in cases:
        got = determine_return_status(expected, attendance)
        assert got == wanted, (expected, attendance, wanted, got)

    print(f"verifier passed: {len(cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
