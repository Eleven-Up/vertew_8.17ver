from conversation import _local_response


def test_local_response_supports_all_mvp_languages():
    assert "RM 8" in _local_response("나시고랭 가격이 얼마예요?", "ko").text
    # No touchscreen on the kiosk: ordering asks for a pay confirmation rather
    # than immediately pointing at a QR code (see test_confirm_payment_reply
    # for the confirmation itself revealing it).
    assert "pay" in _local_response("I want to order", "en").text.lower()
    assert "nasi goreng" in _local_response("Apa yang dijual?", "ms").text


def test_local_response_confirm_payment_reply_differs_from_order_reply():
    order_reply = _local_response("I want to order", "en").text
    pay_reply = _local_response("yes, i'll pay", "en").text
    assert order_reply != pay_reply
    assert "pay" in pay_reply.lower() or "paid" in pay_reply.lower()
