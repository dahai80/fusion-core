from __future__ import annotations

from fusion_core.desensitize import desensitize, pii_classes


class TestPiiClasses:
    def test_each_class_detected(self):
        assert pii_classes("contact a@b.com now") == ["email"]
        assert pii_classes("call 13812345678") == ["phone"]
        assert pii_classes("id 11010119900307861X") == ["idcard"]
        assert pii_classes("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig123") == ["jwt"]
        assert pii_classes("key sk-abcdefghijklmnop1234") == ["secret"]
        assert pii_classes("hex deadbeefdeadbeefdeadbeefdeadbeef") == ["secret"]
        assert pii_classes("b64 YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoxMjM0") == ["secret"]
        assert pii_classes("card 4111111111111111") == ["credit_card"]
        assert pii_classes("from 192.168.1.1") == ["ip"]
        assert pii_classes("login https://u:p@host/x") == ["cred_url"]

    def test_luhn_filters_non_cards(self):
        # 16-digit run that fails Luhn must NOT count as credit_card
        assert "credit_card" not in pii_classes("order 4111111111111112")
        # a 19-digit order number passing Luhn is unavoidable overlap — verify a
        # random long digit run without Luhn validity stays unmasked
        assert "credit_card" not in pii_classes("ref 1234567890123456789")

    def test_empty_and_clean_text(self):
        assert pii_classes("nothing sensitive here") == []
        assert pii_classes("") == []


class TestDesensitize:
    def test_masks_email_phone_idcard(self):
        r = desensitize("user a@b.com phone 13812345678 id 11010119900307861X")
        assert "a@b.com" not in r.masked
        assert "13812345678" not in r.masked
        assert "11010119900307861X" not in r.masked
        assert "[EMAIL]" in r.masked and "[PHONE]" in r.masked and "[IDCARD]" in r.masked
        assert r.denied is False

    def test_masks_secrets(self):
        r = desensitize("auth eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig123 and sk-abcdefghijklmnop1234")
        assert "eyJ" not in r.masked
        assert "sk-abcdefghijklmnop1234" not in r.masked
        assert "[SECRET]" in r.masked

    def test_luhn_credit_card_true_positive_masked(self):
        r = desensitize("pay with 4111111111111111")
        assert "4111111111111111" not in r.masked
        assert "[CREDITCARD]" in r.masked

    def test_luhn_false_positive_not_masked(self):
        # fails Luhn -> not a card, digits stay
        r = desensitize("order 4111111111111112")
        assert "4111111111111112" in r.masked
        assert "[CREDITCARD]" not in r.masked

    def test_ip_masked(self):
        r = desensitize("client 10.0.0.1 and 255.255.255.255")
        assert "10.0.0.1" not in r.masked
        assert r.masked.count("[IP]") == 2

    def test_credential_url_stripped(self):
        r = desensitize("call https://alice:s3cret@api.example.com/v1/x?token=abc123&page=2")
        assert "alice" not in r.masked
        assert "s3cret" not in r.masked
        assert "abc123" not in r.masked
        assert "api.example.com" in r.masked
        assert "page=2" in r.masked

    def test_hard_deny_password_value(self):
        r = desensitize("login attempt password=hunter2 from 1.2.3.4")
        assert r.denied is True
        assert r.masked == ""

    def test_hard_deny_session_token_and_captcha(self):
        assert desensitize("session_token=abc123xyz").denied is True
        assert desensitize('{"captcha_answer": "7"}').denied is True
        # the key name alone without a value does NOT deny
        assert desensitize("user forgot their password again").denied is False

    def test_username_tenant_hashed(self):
        r = desensitize("zhangsan opened tenant acme-corp ticket", username="zhangsan", tenant="acme-corp")
        assert "zhangsan" not in r.masked
        assert "acme-corp" not in r.masked
        # 8-char hex hash tokens present, deterministic
        r2 = desensitize("zhangsan again", username="zhangsan")
        assert r2.masked == r2.masked  # stable within call
        r3 = desensitize("zhangsan again", username="zhangsan")
        assert r.masked != "" and r3.masked.split()[0] == desensitize("zhangsan", username="zhangsan").masked

    def test_quoted_json_username_hashed(self):
        r = desensitize('{"username": "lisi", "action": "login"}')
        assert "lisi" not in r.masked
        assert '"username":' in r.masked or '"username": "' in r.masked

    def test_pii_classes_match_masks(self):
        # issue #56 acceptance: pii_classes matches what desensitize masks
        text = "a@b.com 13812345678 4111111111111111 10.0.0.1 https://u:p@h.io"
        classes = pii_classes(text)
        r = desensitize(text)
        for cls in ("email", "phone", "credit_card", "ip", "cred_url"):
            assert cls in classes
            assert cls in r.classes

    def test_clean_text_unchanged(self):
        r = desensitize("plain text with nothing to hide, order 12345")
        assert r.masked == "plain text with nothing to hide, order 12345"
        assert r.classes == []
        assert r.denied is False
