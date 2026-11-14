# CryptoSplit 실행 환경 이미지 — "파이썬 + 의존성"만 담는다.
#
# 코드는 이미지에 굽지 않고 docker-compose.yml 이 프로젝트 폴더를 /app 에 바인드 마운트한다.
#   - 코드 수정 → cs-restart 만으로 반영 (이미지 재빌드 불필요, 기존 운영 흐름 유지)
#   - 웹 설정 탭이 .env / config/settings.yaml 을 직접 다시 쓰는데, python-dotenv 는
#     임시파일 → rename 방식이라 "파일 하나" 바인드 마운트로는 저장이 실패한다(EBUSY).
#     폴더 통째 마운트가 이를 피한다.
# requirements.txt 가 바뀌었을 때만 재빌드한다 (cs-rebuild).
FROM python:3.14-slim

# 호스트 사용자와 UID/GID를 맞춰 data/·logs/·설정 파일 권한이 꼬이지 않게 한다
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} app && useradd -u ${UID} -g ${GID} -m -s /usr/sbin/nologin app

# tzdata: slim 이미지엔 시간대 DB가 없어 TZ=Asia/Seoul 이 무시된다(로그·백업 파일명·결산 경계)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Seoul \
    CRYPTOSPLIT_IN_DOCKER=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

USER app
WORKDIR /app
CMD ["python", "main.py"]
