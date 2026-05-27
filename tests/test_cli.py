from krita_agent_bridge.cli import build_parser


def test_parser_accepts_status() -> None:
    args = build_parser().parse_args(["status"])
    assert args.command == "status"


def test_parser_accepts_doctor() -> None:
    args = build_parser().parse_args(["doctor"])
    assert args.command == "doctor"
