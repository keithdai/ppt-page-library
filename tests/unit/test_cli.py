from pptlib.cli import build_parser


def test_cli_exposes_required_foundation_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["init"]).command == "init"
    assert parser.parse_args(["serve"]).command == "serve"
    assert parser.parse_args(["worker", "--once"]).once is True
    assert parser.parse_args(["import", "./sources"]).root.name == "sources"
