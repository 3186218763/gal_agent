from src.story import SCRIPT_PACK_SCHEMA_VERSION


def test_story_package_exposes_schema_version():
    assert SCRIPT_PACK_SCHEMA_VERSION == "1.0"
