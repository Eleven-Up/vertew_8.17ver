from conversation import _local_response


def test_local_response_supports_all_mvp_languages():
    assert "RM 8" in _local_response("나시고랭 가격이 얼마예요?", "ko").text
    assert "QR" in _local_response("I want to order", "en").text
    assert "nasi goreng" in _local_response("Apa yang dijual?", "ms").text
