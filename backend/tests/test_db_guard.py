from db_guard import destructive_tests_refusal


def test_allows_local_dev_database():
    assert destructive_tests_refusal("postgresql://pppoe:pppoe@localhost:5432/pppoe", {}) is None
    assert destructive_tests_refusal("postgresql://u:p@127.0.0.1/pppoe_test", {}) is None


def test_refuses_compose_production_database():
    reason = destructive_tests_refusal("postgresql://pppoe:secret@db:5432/pppoe", {})
    assert reason is not None
    assert "ALLOW_DESTRUCTIVE_TESTS" in reason


def test_refuses_unknown_database_name_even_on_localhost():
    assert destructive_tests_refusal("postgresql://u:p@localhost/customers", {}) is not None


def test_explicit_override_allows_any_database():
    assert (
        destructive_tests_refusal("postgresql://u:p@db/pppoe", {"ALLOW_DESTRUCTIVE_TESTS": "1"}) is None
    )
