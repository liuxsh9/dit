from dit.core.hash import canonical_json, row_hash, query_fingerprint


class TestCanonicalJson:
    def test_key_order_irrelevant(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert canonical_json(a) == canonical_json(b)

    def test_whitespace_irrelevant(self):
        import json
        obj = {"messages": [{"role": "user", "content": "hello"}]}
        compact = json.dumps(obj, separators=(",", ":"))
        pretty = json.dumps(obj, indent=2)
        assert canonical_json(json.loads(compact)) == canonical_json(json.loads(pretty))

    def test_unicode_normalized(self):
        obj = {"text": "café"}
        result = canonical_json(obj)
        assert isinstance(result, bytes)

    def test_deterministic(self):
        obj = {"messages": [{"role": "user", "content": "test"}]}
        assert canonical_json(obj) == canonical_json(obj)


class TestRowHash:
    def test_returns_hex_string_64_chars(self):
        obj = {"messages": [{"role": "user", "content": "hello"}]}
        h = row_hash(obj)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_content_same_hash(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert row_hash(a) == row_hash(b)

    def test_different_content_different_hash(self):
        a = {"messages": [{"role": "user", "content": "hello"}]}
        b = {"messages": [{"role": "user", "content": "world"}]}
        assert row_hash(a) != row_hash(b)


class TestQueryFingerprint:
    def test_extracts_user_content(self):
        conv = {
            "messages": [
                {"role": "user", "content": "Implement LRU cache"},
                {"role": "assistant", "content": "Here's the code..."},
            ]
        }
        fp = query_fingerprint(conv)
        assert len(fp) == 64

    def test_same_query_same_fingerprint(self):
        a = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "response A"},
            ]
        }
        b = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "response B"},
            ]
        }
        assert query_fingerprint(a) == query_fingerprint(b)

    def test_different_query_different_fingerprint(self):
        a = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
        }
        b = {
            "messages": [
                {"role": "user", "content": "world"},
                {"role": "assistant", "content": "hi"},
            ]
        }
        assert query_fingerprint(a) != query_fingerprint(b)

    def test_no_user_message_returns_none(self):
        conv = {"messages": [{"role": "assistant", "content": "hi"}]}
        assert query_fingerprint(conv) is None

    def test_multi_turn_concatenates_user_messages(self):
        conv = {
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "..."},
                {"role": "user", "content": "second"},
                {"role": "assistant", "content": "..."},
            ]
        }
        fp = query_fingerprint(conv)
        assert fp is not None
        single = {
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "..."},
            ]
        }
        assert query_fingerprint(single) != fp
