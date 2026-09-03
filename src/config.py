"""전역 설정과 사이트 정보.

비밀값(API 키, 워드프레스 비밀번호)은 .env 에서 읽는다. 절대 코드에 적지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# 콘텐츠 생성에 사용할 Claude 모델.
# 기본은 지능/비용 균형이 좋은 claude-opus-4-8.
# 더 어려운 장기 작업이 필요하면 "claude-fable-5" 로 바꿀 수 있다.
MODEL = os.getenv("AUTOBLOG_MODEL", "claude-opus-4-8")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


@dataclass(frozen=True)
class Site:
    """워드프레스 사이트 한 곳의 발행 정보."""

    key: str          # 내부 식별자 (CLI --site 값)
    name: str         # 사람이 읽는 이름
    niche: str        # 분야
    env_prefix: str   # .env 변수 접두사 (WP_<PREFIX>_URL 등)

    @property
    def url(self) -> str | None:
        return os.getenv(f"WP_{self.env_prefix}_URL")

    @property
    def user(self) -> str | None:
        return os.getenv(f"WP_{self.env_prefix}_USER")

    @property
    def app_password(self) -> str | None:
        return os.getenv(f"WP_{self.env_prefix}_APP_PASSWORD")

    @property
    def configured(self) -> bool:
        return all([self.url, self.user, self.app_password])


# 자동 발행이 가능한 워드프레스 사이트들.
# 티스토리(finwiz 등)·네이버는 REST 발행이 안 되므로 여기 넣지 않는다
# (초안 생성까지만 자동화, 발행은 수동).
SITES: dict[str, Site] = {
    "bodybalance": Site(
        key="bodybalance",
        name="bodybalance-labo.com",
        niche="건강(스트레스·수면·노화·영양·다이어트·질환관리)",
        env_prefix="BODYBALANCE",
    ),
    "seaga": Site(
        key="seaga",
        name="seaga.co.kr",
        niche="잡블로그(이슈·정책)",
        env_prefix="SEAGA",
    ),
    "calculator": Site(
        key="calculator",
        name="calculator.seaga.co.kr",
        niche="계산기/유틸리티",
        env_prefix="CALCULATOR",
    ),
}


def get_site(key: str) -> Site:
    if key not in SITES:
        available = ", ".join(SITES)
        raise KeyError(f"알 수 없는 사이트 '{key}'. 사용 가능: {available}")
    return SITES[key]


def require_api_key() -> str:
    # 실행 중에 .env 를 채우는 경우(UI 에서 키 저장)도 있으므로 그때그때 읽는다.
    key = os.getenv("ANTHROPIC_API_KEY") or ANTHROPIC_API_KEY
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하세요."
        )
    return key
