"""거래소 래퍼 전용 예외."""


class BithumbResponseError(Exception):
    """빗썸이 응답은 줬는데 기대한 형식이 아닐 때 (점검·장애 중 자주 발생).

    python_bithumb 은 응답 형식을 검증하지 않아서 이런 경우 `string indices must
    be integers` 같은 엉뚱한 TypeError/AttributeError 로 터진다. 래퍼가 이를 잡아
    실제 응답 일부를 담은 이 예외로 바꿔 던진다 (exchange/bithumb_client.py).
    인증 오류가 아니므로 재시도 대상이다 (exchange/retry.py).
    """
