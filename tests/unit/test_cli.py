from pptlib.cli import build_parser


def test_cli_exposes_required_foundation_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["init"]).command == "init"
    assert parser.parse_args(["worker", "--once"]).once is True
    assert parser.parse_args(["render-missing"]).command == "render-missing"
    scan_plan = parser.parse_args(["scan-plan", "history", "--limit", "25"])
    assert scan_plan.command == "scan-plan"
    assert scan_plan.scan_action == "history"
    assert scan_plan.limit == 25
    duplicates = parser.parse_args(["duplicates", "--refresh"])
    assert duplicates.command == "duplicates"
    assert duplicates.refresh is True
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
    assert compose.preflight_token is None
    assert (
        parser.parse_args(
            ["compose", "./sel.json", "./out.pptx", "--preflight-token", "abc"]
        ).preflight_token
        == "abc"
    )
    preflight = parser.parse_args(["compose-preflight", "./sel.json", "./out.pptx"])
    assert preflight.command == "compose-preflight"
    assert preflight.no_verify_hash is False
    assert (
        parser.parse_args(
            ["compose", "./sel.json", "./out.pptx", "--no-verify-hash"]
        ).no_verify_hash
        is True
    )

    catalog = parser.parse_args(["catalog", "./out"])
    assert catalog.command == "catalog"
    assert catalog.output_dir.name == "out"
    assert catalog.include_local_fields is False
    assert (
        parser.parse_args(["catalog", "./out", "--include-local-fields"]).include_local_fields
        is True
    )

    sync = parser.parse_args(["sync", "--app-id", "app_123", "--dry-run"])
    assert sync.command == "sync"
    assert sync.app_id == "app_123"
    assert sync.environment == "online"
    assert sync.dry_run is True
