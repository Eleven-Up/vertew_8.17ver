from conversation import _local_response


def test_local_response_supports_all_mvp_languages():
    assert "RM 5" in _local_response("망고 가격이 얼마예요?", "ko").text
    assert "QR" in _local_response("I want to order", "en").text
    assert "tembikai" in _local_response("Apa yang dijual?", "ms").text
