from __future__ import annotations

from datetime import datetime
from itertools import cycle
from xml.etree import ElementTree

import requests

from app.config import AppConfig
from app.models import RankedTopic, TopicCandidate, TopicDetail


QUOTE_THEMES = [
    "흔들릴수록 다시 붙잡아야 하는 마음에 대한 문장",
    "자존감을 잃지 않게 도와주는 짧은 통찰",
    "관계를 단단하게 만드는 현실형 조언",
    "불안을 견디게 해주는 저녁 한 문장",
    "작은 습관 하나로 하루를 바꾸는 이야기",
]

POEM_THEMES = [
    "밤에 읽기 좋은 차분한 위로의 문장",
    "계절을 다정하게 건너는 감성 문장",
    "위로가 필요한 날 조용히 머무는 시",
    "다시 시작할 용기를 건네는 산문",
    "혼자 있는 시간을 따뜻하게 만드는 글",
]

STORY_ARCHETYPES = [
    {
        "key": "inheritance_property",
        "theme": "장례식이 끝난 뒤 유언장 한 줄이 가족을 흔드는 상속 사연",
        "title_angle": "가족 호칭과 집·유산·유언 같은 생활 단어를 먼저 드러내고, 마지막 한 선택이 판을 바꾸는 제목",
        "hook": "상복도 벗기 전에 집을 비우라는 말이나 숨겨진 계약서가 드러나는 순간처럼 관계가 무너지는 장면을 먼저 보여준다.",
        "escalation": "형제 간 오래된 서운함, 숨겨진 채무, 집 명의 문제처럼 매 장면마다 새로운 사실이 밝혀지게 만든다.",
        "ending": "돈보다 존엄과 관계의 본질을 선택하는 결말로 가되, 한 사람의 결단이 전체 분위기를 바꾸게 만든다.",
    },
    {
        "key": "late_life_romance",
        "theme": "황혼에 다시 사랑을 시작한 어머니를 가족이 반대하면서 벌어지는 사연",
        "title_angle": "황혼 사랑, 재혼, 마지막 선택처럼 감정과 결정을 함께 보여주는 제목",
        "hook": "가족 식사 자리에서 관계를 공개하거나 여행 가방을 든 순간처럼 감정 충돌이 즉시 보이는 장면으로 연다.",
        "escalation": "자녀의 오해, 주변 시선, 과거 상처가 순서대로 드러나며 누구도 쉽게 악역으로 보이지 않게 쌓아 올린다.",
        "ending": "사랑과 자존심, 가족의 체면 사이에서 스스로를 선택하는 따뜻한 후반부로 마무리한다.",
    },
    {
        "key": "workplace_reversal",
        "theme": "퇴직 후 무시받던 아버지가 예상 못 한 한 통의 전화로 다시 평가받는 사연",
        "title_angle": "무시, 경비원, 퇴직, 전화 한 통처럼 현실적인 굴욕과 반전 계기를 함께 붙인 제목",
        "hook": "사람들 앞에서 모욕을 당하거나 쫓겨날 위기에 놓인 장면부터 보여준다.",
        "escalation": "과거 경력의 비밀, 억울한 누명, 주변 인물의 태도 변화가 차례로 이어지게 만든다.",
        "ending": "복수보다 품격 있는 반전, 그리고 스스로를 다시 세우는 회복의 감정으로 끝낸다.",
    },
    {
        "key": "caregiving_health",
        "theme": "요양병원 앞에서 멈춰 선 자식이 늦게 알게 된 간병의 진심에 관한 사연",
        "title_angle": "병원, 간병, 마지막 부탁, 늦게 알았다 같은 단어로 죄책감과 진실을 동시에 건드리는 제목",
        "hook": "입원 서류를 앞에 두고 손이 멈추는 장면이나 의사의 한마디가 심장을 치는 순간으로 시작한다.",
        "escalation": "형제 간 부담 갈등, 보호자의 거짓말, 부모가 숨긴 선택이 하나씩 드러나야 한다.",
        "ending": "가장 늦었다고 느껴지는 순간에 겨우 닿는 이해와 화해로 정리한다.",
    },
    {
        "key": "retirement_money",
        "theme": "은퇴 자금 보증을 부탁받은 뒤 노후가 무너질 뻔한 가족 금전 사연",
        "title_angle": "보증, 퇴직금, 노후, 동생이나 자식처럼 구체적 관계와 돈 문제를 함께 드러내는 제목",
        "hook": "도장 하나만 찍어 달라는 부탁이 사실상 인생 전체를 흔드는 순간을 바로 보여준다.",
        "escalation": "채무, 거짓말, 몰래 팔린 자산, 끊어진 연락이 단계적으로 나오게 만든다.",
        "ending": "돈을 잃을 뻔한 위기보다 자기 존엄을 지킨 결단이 더 크게 남는 마무리로 간다.",
    },
    {
        "key": "blended_family",
        "theme": "재혼 가정에서 끝내 입 밖에 못 낸 한마디가 모두를 흔드는 사연",
        "title_angle": "재혼, 며느리, 사위, 새가족, 한마디처럼 관계의 미묘함을 제목에서 바로 보이게 한다.",
        "hook": "명절 식탁이나 집안 행사에서 감정이 터지기 직전인 장면을 먼저 배치한다.",
        "escalation": "서운함의 원인, 오해의 출발점, 숨겨진 배려가 차례로 드러나게 만든다.",
        "ending": "누가 완전히 이기기보다 서로의 상처를 보게 되는 여운 있는 화해 쪽으로 마무리한다.",
    },
    {
        "key": "neighbor_misunderstanding",
        "theme": "동네 이웃과 층간소음 오해로 시작된 갈등이 뜻밖의 사연으로 뒤집히는 이야기",
        "title_angle": "동네, 아파트, 층간소음, 억울함, 반전처럼 일상적인 공간과 감정어를 함께 묶는 제목",
        "hook": "문을 세게 두드리는 순간이나 관리사무소 신고 직전 장면으로 시작한다.",
        "escalation": "서로 다른 사정, 건강 문제, 가족 비밀이 하나씩 나오며 판단이 계속 흔들리게 만든다.",
        "ending": "이웃 간 적대가 예상 밖 연대로 바뀌는 순간을 남긴다.",
    },
    {
        "key": "filial_duty_self_respect",
        "theme": "평생 가족만 위해 살던 어머니가 처음으로 자신을 선택한 날의 사연",
        "title_angle": "평생, 가족만, 마지막 선택, 여행처럼 희생과 결심을 동시에 보여주는 제목",
        "hook": "조용히 떠날 준비를 마친 순간이나 가족에게 처음으로 거절을 말하는 장면으로 연다.",
        "escalation": "자녀들의 당연함, 억눌린 욕망, 오래된 포기가 단계적으로 드러난다.",
        "ending": "미안함보다 자기 삶을 회복하는 해방감과 가족의 뒤늦은 이해를 남긴다.",
    },
    {
        "key": "sibling_conflict",
        "theme": "부모 병간호를 두고 형제자매의 민낯이 드러나는 사연",
        "title_angle": "간병, 형제, 부모 집, 병원비처럼 생활 밀착 갈등 키워드를 직접적으로 붙인 제목",
        "hook": "병원비 정산이나 보호자 교대 순간처럼 더 미룰 수 없는 갈등 장면으로 시작한다.",
        "escalation": "희생의 불균형, 오래된 차별 기억, 뒤늦게 밝혀진 부모 마음이 이어진다.",
        "ending": "모든 관계가 회복되지는 않더라도 최소한의 진실과 선을 남기는 방향으로 끝낸다.",
    },
    {
        "key": "scam_recovery",
        "theme": "노후 자금을 잃을 뻔한 중장년 부부가 사기를 알아차리고 되찾는 사연",
        "title_angle": "노후자금, 투자, 보이스피싱, 되찾았다 같은 실익형 반전 제목",
        "hook": "송금 직전 멈춘 손, 경찰 전화, 사기범과의 통화처럼 긴박한 장면으로 출발한다.",
        "escalation": "신뢰했던 사람, 작은 욕심, 반복된 압박이 실제 행동으로 이어지게 만든다.",
        "ending": "피해를 막은 안도감과 다시는 같은 실수를 반복하지 않겠다는 결심으로 마무리한다.",
    },
    {
        "key": "reunion_redemption",
        "theme": "오래 끊긴 가족이 뜻밖의 장소에서 다시 만나 서로를 이해하게 되는 사연",
        "title_angle": "다시 만난, 십수 년 만에, 뜻밖의 장소, 못다 한 말처럼 재회 감정을 먼저 건드린다.",
        "hook": "우연한 재회 직후 서로 말을 잇지 못하는 몇 초를 먼저 보여준다.",
        "escalation": "헤어졌던 이유, 각자의 오해, 전달되지 못한 진심이 한 겹씩 벗겨진다.",
        "ending": "완전한 해피엔딩보다 다시 이어질 수 있는 여지를 남기는 여운형 결말로 정리한다.",
    },
    {
        "key": "community_respect",
        "theme": "동네에서 무시받던 중장년 여성이 예상 못 한 순간 존중을 되찾는 사연",
        "title_angle": "무시, 시장, 반찬가게, 동네, 한순간에 달라진 시선처럼 공간과 감정의 변화를 제목에 담는다.",
        "hook": "사람들 앞에서 체면이 무너지는 장면을 먼저 보여주고 왜 그런 일이 생겼는지 궁금하게 만든다.",
        "escalation": "오해, 험담, 숨겨진 실력이나 선행이 단계적으로 드러나며 분위기를 반전시킨다.",
        "ending": "통쾌함 뒤에 남는 품위와 자기 회복을 함께 남긴다.",
    },
]

STORY_THEMES = [item["theme"] for item in STORY_ARCHETYPES]


class LifeContentCollector:
    """Provide non-news topic seeds for quotes, poems, and story channels."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config

    def collect(self, mode: str, limit: int = 5) -> list[TopicCandidate]:
        if mode == "quotes":
            values = self._collect_quote_themes(limit) or self._cycle_values(QUOTE_THEMES, limit)
        elif mode == "poems":
            values = self._cycle_values(POEM_THEMES, limit)
        elif mode == "stories":
            values = self._collect_story_themes(limit)
        else:
            values = self._cycle_values(QUOTE_THEMES, limit)

        return [
            TopicCandidate(
                title=value,
                source=mode,
                weight=1.0,
                metadata={"content_mode": mode},
            )
            for value in values
        ]

    def collect_details(self, topic: RankedTopic, mode: str) -> list[TopicDetail]:
        if mode == "quotes":
            return [
                TopicDetail(
                    title="핵심 메시지",
                    summary=f"{topic.representative_title}를 지금의 감정과 연결되는 짧은 문장으로 다시 정리합니다.",
                    source="quotes",
                ),
                TopicDetail(
                    title="감정 연결",
                    summary="위로에서 끝내지 않고 오늘 바로 붙잡을 수 있는 태도와 선택으로 이어지게 만듭니다.",
                    source="quotes",
                ),
                TopicDetail(
                    title="실전 적용",
                    summary="저장하고 싶은 한 줄, 댓글 달고 싶은 한 줄, 누군가에게 보내고 싶은 한 줄이 함께 살아나게 구성합니다.",
                    source="quotes",
                ),
            ]

        if mode == "poems":
            return [
                TopicDetail(
                    title="분위기",
                    summary=f"{topic.representative_title}를 차분하고 선명한 감정으로 풀어내는 정서를 우선합니다.",
                    source="poems",
                ),
                TopicDetail(
                    title="해석",
                    summary="과한 설명보다 일상에서 바로 닿는 감정으로 바꾸어 들려줍니다.",
                    source="poems",
                ),
                TopicDetail(
                    title="마무리",
                    summary="짧게 남지만 오래 머무는 한 줄로 정리되도록 만듭니다.",
                    source="poems",
                ),
            ]

        archetype = self._story_archetype_for_topic(topic)
        return [
            TopicDetail(
                title="고조회수 제목 패턴",
                summary=f"{archetype['title_angle']}처럼 관계와 선택의 충돌이 한눈에 보이는 제목 구조를 참고하되, 실제 인기 영상의 제목을 그대로 베끼지 않습니다.",
                source="stories",
            ),
            TopicDetail(
                title="초반 훅",
                summary=f"{archetype['hook']} 같은 장면처럼 첫 20~40초 안에 가장 센 감정 충돌을 먼저 보여줍니다.",
                source="stories",
            ),
            TopicDetail(
                title="중반 전개",
                summary=f"{archetype['escalation']} 흐름처럼 장면마다 새 사건과 새 사실이 나오게 구성합니다.",
                source="stories",
            ),
            TopicDetail(
                title="후반 해소",
                summary=f"{archetype['ending']} 결로 감정을 정리하되, 지나친 교훈문 반복은 피합니다.",
                source="stories",
            ),
            TopicDetail(
                title="차별화 포인트",
                summary="한 영상에는 한 이야기만 쓰고, 인기 사연 채널의 구조만 참고하며 실제 문장, 전개 순서, 인물 설정, 대사, 제목 문구는 새롭게 각색합니다.",
                source="stories",
            ),
        ]

    def _collect_quote_themes(self, limit: int) -> list[str]:
        if self.config is None or not self.config.allow_network:
            return []

        try:
            response = requests.get(
                f"https://trends.google.com/trending/rss?geo={self.config.collection.google_trends_geo}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            root = ElementTree.fromstring(response.text)
        except Exception:
            return []

        themes: list[str] = []
        seen: set[str] = set()
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            theme = self._quote_theme_from_signal(title)
            if theme in seen:
                continue
            seen.add(theme)
            themes.append(theme)
            if len(themes) >= limit:
                break
        return themes

    def _collect_story_themes(self, limit: int) -> list[str]:
        archetypes = list(STORY_ARCHETYPES)
        if not archetypes:
            return []

        start = self._story_rotation_seed() % len(archetypes)
        step = 5 if len(archetypes) > 5 else 1
        used: set[int] = set()
        ordered: list[str] = []
        index = start

        while len(ordered) < min(limit, len(archetypes)):
            if index not in used:
                ordered.append(archetypes[index]["theme"])
                used.add(index)
            index = (index + step) % len(archetypes)

        while len(ordered) < limit:
            ordered.extend(self._cycle_values(STORY_THEMES, limit - len(ordered)))
        return ordered[:limit]

    def _story_rotation_seed(self) -> int:
        now = datetime.now()
        return now.toordinal() * 10 + (now.hour // 6)

    @staticmethod
    def _cycle_values(values: list[str], limit: int) -> list[str]:
        picked: list[str] = []
        iterator = cycle(values)
        for _ in range(limit):
            picked.append(next(iterator))
        return picked

    def _story_archetype_for_topic(self, topic: RankedTopic) -> dict[str, str]:
        haystack = " ".join(
            [
                topic.representative_title,
                topic.normalized_topic,
                *topic.keywords,
                *topic.mentions,
            ]
        ).lower()

        keyword_map = {
            "inheritance_property": ("유언", "상속", "집", "명의", "재개발", "장례"),
            "late_life_romance": ("황혼", "사랑", "연애", "재혼", "여행"),
            "workplace_reversal": ("퇴직", "경비", "택배", "무시", "직장"),
            "caregiving_health": ("병원", "간병", "치매", "요양", "수술"),
            "retirement_money": ("보증", "퇴직금", "노후", "사기", "투자"),
            "blended_family": ("재혼가정", "며느리", "사위", "새가족", "명절"),
            "neighbor_misunderstanding": ("층간소음", "이웃", "아파트", "동네", "관리사무소"),
            "filial_duty_self_respect": ("어머니", "평생", "가족만", "자신", "선택"),
            "sibling_conflict": ("형제", "남매", "병간호", "부모", "병원비"),
            "scam_recovery": ("보이스피싱", "사기", "송금", "노후자금", "투자"),
            "reunion_redemption": ("재회", "십수년", "다시 만난", "못다한 말", "헤어진"),
            "community_respect": ("시장", "반찬가게", "동네", "무시", "존중"),
        }
        for archetype in STORY_ARCHETYPES:
            if any(token in haystack for token in keyword_map.get(archetype["key"], ())):
                return archetype

        index = abs(hash(topic.normalized_topic or topic.representative_title)) % len(STORY_ARCHETYPES)
        return STORY_ARCHETYPES[index]

    @staticmethod
    def _quote_theme_from_signal(title: str) -> str:
        signal = title.replace(" ", "")
        keyword_map = [
            (("연금", "퇴직", "물가", "세금", "금리", "부동산"), "돈 앞에서 흔들리지 않게 도와주는 문장"),
            (("이혼", "가족", "부모", "형제", "남편", "아내"), "가족에게 상처받았을 때 필요한 문장"),
            (("이직", "직장", "회사", "채용", "실업"), "버티기 지칠 때 필요한 문장"),
            (("건강", "병원", "질병", "치매", "수술"), "불안을 견디게 해주는 저녁 문장"),
            (("사기", "배신", "갈등", "고소"), "관계를 정리할 용기가 필요할 때의 문장"),
            (("학교", "교육", "입시", "시험"), "비교에 지쳤을 때 필요한 문장"),
        ]
        for keywords, theme in keyword_map:
            if any(keyword in signal for keyword in keywords):
                return theme
        return "하루 끝에 조용히 마음을 붙잡아 주는 문장"
