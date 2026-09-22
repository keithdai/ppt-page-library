from pptlib.cli import build_parser


def test_cli_exposes_required_foundation_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["init"]).command == "init"
    assert parser.parse_args(["serve"]).command == "serve"
    assert parser.parse_args(["worker", "--once"]).once is True
    # import now accepts one or more files/directories, indexed in place
    single = parser.parse_args(["import", "./sources"]).root
    assert [p.name for p in single] == ["sources"]
    multi = parser.parse_args(["import", "./a.pptx", "./b.pptx"]).root
    assert [p.name for p in multi] == ["a.pptx", "b.pptx"]

    compose = parser.parse_args(["compose", "./sel.json", "./out.pptx"])
    assert compose.command == "compose"
    assert compose.manifest.name == "sel.json"
    assert compose.output.name == "out.pptx"
    assert compose.no_verify_hash is False
    assert parser.parse_args(
        ["compose", "./sel.json", "./out.pptx", "--no-verify-hash"]
    ).no_verify_hash is True

    catalog = parser.parse_args(["catalog", "./out"])
    assert catalog.command == "catalog"
    assert catalog.output_dir.name == "out"

    sync = parser.parse_args(["sync", "--app-id", "app_123", "--dry-run"])
    assert sync.command == "sync"
    assert sync.app_id == "app_123"
    assert sync.environment == "online"
    assert sync.dry_run is True
