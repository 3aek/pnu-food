"""
오늘 뭐 먹지? 부산대
- 질문 6개에 답하면 부산대 근처 식당을 추천하는 Streamlit 앱
- 위치·시간·예산은 조건으로 거르고, 종류·형태·온도는 점수로 순위를 매김
"""

import csv
import html
import random
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

# ─────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────
APP_TITLE = "오늘 뭐 먹지? 부산대"
DATA_FILE = Path(__file__).parent / "restaurants.csv"
KST = ZoneInfo("Asia/Seoul")          # 서버가 해외에 있어도 한국 시간으로 계산
SCHOOL_CAFETERIA = "금정회관 (학생식당)"

ANY = "상관없음"

# 질문 목록: (저장 이름, 질문 문장, 선택지)
QUESTIONS = [
    ("location", "어디 근처인가요?", ["정문", "북문", ANY]),
    ("when", "언제 드실 건가요?", ["지금", "점심 (12시)", "저녁 (18시)"]),
    ("budget", "1인 예산은 어느 정도인가요?",
     ["7천 원 이하", "1만 원 이하", "1만 원 초과도 괜찮음", ANY]),
    ("kind", "어떤 종류를 원하세요?", ["한식", "양식", "중식", "일식", "기타", ANY]),
    ("form", "밥, 면, 빵 중에서 골라주세요.", ["밥", "면", "빵", ANY]),
    ("temp", "따뜻한 음식과 차가운 음식 중 어느 쪽인가요?",
     ["따뜻한 음식", "차가운 음식", ANY]),
]

BUDGET_LIMIT = {"7천 원 이하": 7000, "1만 원 이하": 10000}
TEMP_MAP = {"따뜻한 음식": "뜨거움", "차가운 음식": "차가움"}
WEEKDAYS = "월화수목금토일"           # datetime.weekday(): 월=0 ... 일=6


# ─────────────────────────────────────────────
# 데이터 읽기
# ─────────────────────────────────────────────
def load_restaurants(path=DATA_FILE):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["가격"] = int(float(r["1인 가격"])) if r["1인 가격"] else 0
    return rows


# ─────────────────────────────────────────────
# 영업 여부 판단
# ─────────────────────────────────────────────
def parse_range(text):
    """'10:00-21:00' → (time(10,0), time(21,0)). '없음'이나 빈칸이면 None."""
    text = (text or "").strip()
    if not text or text == "없음" or "-" not in text:
        return None
    start, end = text.split("-", 1)
    h1, m1 = map(int, start.strip().split(":"))
    h2, m2 = map(int, end.strip().split(":"))
    end_t = time(23, 59, 59) if h2 >= 24 else time(h2, m2)
    return time(h1, m1), end_t


def in_range(t, rng):
    start, end = rng
    if start <= end:
        return start <= t < end
    return t >= start or t < end      # 새벽까지 영업 (예: 18:00-02:00)


def is_day_off(text, dt):
    """휴무일 칸을 보고 그날 쉬는지 판단."""
    text = (text or "").strip()
    if not text or text == "없음":
        return False
    if "둘째" in text and "일요일" in text:          # 매달 둘째 일요일
        return dt.weekday() == 6 and 8 <= dt.day <= 14
    days = [d.strip() for d in text.split(",")]
    return WEEKDAYS[dt.weekday()] in days


def is_open(r, dt):
    if is_day_off(r["휴무일"], dt):
        return False
    hours = parse_range(r["영업시간"])
    if hours is None or not in_range(dt.time(), hours):
        return False
    brk = parse_range(r["브레이크타임"])
    if brk is not None and in_range(dt.time(), brk):
        return False
    return True


def target_time(when, now=None):
    now = now or datetime.now(KST)
    if when == "점심 (12시)":
        return now.replace(hour=12, minute=0, second=0, microsecond=0)
    if when == "저녁 (18시)":
        return now.replace(hour=18, minute=0, second=0, microsecond=0)
    return now


# ─────────────────────────────────────────────
# 추천 로직
# ─────────────────────────────────────────────
def passes_filters(r, a, dt):
    """위치·시간·예산: 반드시 지켜야 하는 조건"""
    if a["location"] != ANY and r["위치"] != a["location"]:
        return False
    if a["budget"] in BUDGET_LIMIT and r["가격"] > BUDGET_LIMIT[a["budget"]]:
        return False
    return is_open(r, dt)


def matched_prefs(r, a):
    """종류·형태·온도 중 맞는 항목 이름 목록 (점수 = 개수)"""
    hits = []
    if a["kind"] != ANY and r["종류"] == a["kind"]:
        hits.append(a["kind"])
    if a["form"] != ANY and a["form"] in [x.strip() for x in r["형태"].split(",")]:
        hits.append(a["form"])
    if a["temp"] != ANY and r["온도"] in (TEMP_MAP[a["temp"]], "둘다"):
        hits.append(a["temp"])
    return hits


def requested_prefs(a):
    return sum(1 for k in ("kind", "form", "temp") if a[k] != ANY)


def all_any(a):
    """시간을 뺀 5개 질문이 모두 '상관없음'인지"""
    return all(a[k] == ANY for k in ("location", "budget", "kind", "form", "temp"))


def build_recommendation(rows, a, now=None):
    """
    추천 목록과 안내 문구를 만든다.
    반환: {"queue": [식당, ...], "notice": 문구 또는 None, "partial": bool}
    """
    dt = target_time(a["when"], now)
    candidates = [r for r in rows if passes_filters(r, a, dt)]

    # 모두 '상관없음' → 학식 우선
    if all_any(a):
        cafeteria = [r for r in candidates if r["식당명"] == SCHOOL_CAFETERIA]
        others = [r for r in candidates if r["식당명"] != SCHOOL_CAFETERIA]
        random.shuffle(others)
        if cafeteria:
            return {"queue": cafeteria + others,
                    "notice": "고르기 어려우시면 학식은 어떠세요?",
                    "partial": False}
        return {"queue": others,
                "notice": "학식 운영 시간이 아니라서 영업 중인 식당을 추천합니다.",
                "partial": False}

    # 점수 순 정렬 (같은 점수는 무작위)
    random.shuffle(candidates)
    candidates.sort(key=lambda r: len(matched_prefs(r, a)), reverse=True)

    need = requested_prefs(a)
    best = len(matched_prefs(candidates[0], a)) if candidates else 0
    partial = bool(candidates) and need > 0 and best < need
    return {"queue": candidates, "notice": None, "partial": partial}


def reason_text(r, a):
    parts = []
    if a["location"] != ANY:
        parts.append(a["location"])
    if a["budget"] in BUDGET_LIMIT:
        parts.append(a["budget"])
    parts += matched_prefs(r, a)
    if not parts:
        return "영업 중인 식당 중에서 골랐습니다."
    return f"{', '.join(parts)} 조건에 맞습니다."


SHOW_COUNT = 3   # 한 번에 추천하는 식당 수


def next_batch(queue, seen, shown, why):
    """피드백에 맞는 다음 추천 묶음(최대 3곳)의 위치 목록."""
    cur = [queue[i] for i in shown]
    min_price = min(r["가격"] for r in cur)
    kinds = {r["종류"] for r in cur}
    picks = []
    for i, r in enumerate(queue):
        if i in seen:
            continue
        if why == "price" and r["가격"] >= min_price:
            continue
        if why == "kind" and r["종류"] in kinds:
            continue
        picks.append(i)
        if len(picks) == SHOW_COUNT:
            break
    return picks


# ─────────────────────────────────────────────
# 화면 상태 관리
# ─────────────────────────────────────────────
def init_state():
    defaults = {
        "authed": False,
        "page": "start",      # start → question → result
        "step": 0,
        "answers": {},
        "queue": [],
        "shown": [],          # 지금 보여주는 식당들의 위치
        "seen": set(),        # 이미 보여준 식당들
        "notice": None,
        "partial": False,
        "stage": "show",      # show → why → end
        "again": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def restart():
    st.session_state.update(page="start", step=0, answers={}, queue=[],
                            shown=[], seen=set(), notice=None, partial=False,
                            stage="show", again=False)


def start():
    st.session_state.update(page="question", step=0, answers={})


def choose(key, option):
    st.session_state.answers[key] = option
    if st.session_state.step < len(QUESTIONS) - 1:
        st.session_state.step += 1
    else:
        rec = build_recommendation(load_restaurants(), st.session_state.answers)
        shown = list(range(min(SHOW_COUNT, len(rec["queue"]))))
        st.session_state.update(page="result", queue=rec["queue"], shown=shown,
                                seen=set(shown), notice=rec["notice"],
                                partial=rec["partial"], stage="show",
                                again=False)


def go_back():
    if st.session_state.step == 0:
        st.session_state.page = "start"
    else:
        st.session_state.step -= 1


def want_other():
    st.session_state.stage = "why"


def retry(why):
    s = st.session_state
    picks = next_batch(s.queue, s.seen, s.shown, why)
    if not picks:
        s.stage = "end"
    else:
        s.shown = picks
        s.seen.update(picks)
        s.update(stage="show", again=True, notice=None, partial=False)


# ─────────────────────────────────────────────
# 디자인 (토스 앱처럼: 큰 글씨, 넉넉한 여백, 회색 카드, 파란 버튼)
# ─────────────────────────────────────────────
TOSS_BLUE = "#3182F6"

CSS = f"""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');

html, body, [class*="st-"], button, input, p, div, span {{
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, sans-serif !important;
    letter-spacing: -0.02em;
}}
header[data-testid="stHeader"] {{ background: transparent; }}

/* 글씨 */
.t-title {{ font-size: 28px; font-weight: 700; color: #191F28; line-height: 1.35; margin: 0 0 8px; }}
.t-sub {{ font-size: 17px; color: #6B7684; line-height: 1.5; margin-bottom: 32px; }}
.t-q {{ font-size: 24px; font-weight: 700; color: #191F28; line-height: 1.4; margin: 0 0 24px; }}
.t-note {{ font-size: 15px; color: #8B95A1; margin: -12px 0 16px; }}

/* 진행 표시 */
.t-step {{ font-size: 14px; font-weight: 600; color: {TOSS_BLUE}; margin-bottom: 8px; }}
.t-bar {{ height: 4px; background: #F2F4F6; border-radius: 2px; margin-bottom: 28px; }}
.t-bar > div {{ height: 4px; background: {TOSS_BLUE}; border-radius: 2px; }}

/* 추천 카드 */
.t-card {{ background: #F9FAFB; border-radius: 20px; padding: 20px 22px; margin-bottom: 12px; }}
.t-card .num {{ font-size: 13px; font-weight: 600; color: {TOSS_BLUE}; margin-bottom: 4px; }}
.t-card .name {{ font-size: 20px; font-weight: 700; color: #191F28; margin-bottom: 14px; }}
.t-card .row {{ display: flex; font-size: 15px; color: #333D4B; padding: 3px 0; line-height: 1.45; }}
.t-card .label {{ width: 44px; flex-shrink: 0; color: #8B95A1; }}
.t-card .why {{ margin-top: 12px; font-size: 14px; color: #6B7684; }}

/* 버튼 공통 */
.stButton button, .stFormSubmitButton button {{
    border-radius: 16px; font-size: 17px; font-weight: 600;
    min-height: 56px; box-shadow: none; transition: background .15s;
}}
/* 선택지: 연한 회색 */
.stButton button[kind="secondary"], [data-testid="stBaseButton-secondary"] {{
    background: #F2F4F6; color: #333D4B; border: none;
    justify-content: flex-start; padding-left: 22px;
}}
.stButton button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {{
    background: #E5E8EB; color: #191F28; border: none;
}}
/* 꼭 누르는 버튼: 토스 파랑 */
.stButton button[kind="primary"], [data-testid="stBaseButton-primary"],
.stFormSubmitButton button {{
    background: {TOSS_BLUE}; color: #FFFFFF; border: none;
}}
.stButton button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover,
.stFormSubmitButton button:hover {{
    background: #1B64DA; color: #FFFFFF; border: none;
}}
/* 이전 / 처음부터 다시: 회색 글자 */
.stButton button[kind="tertiary"], [data-testid="stBaseButton-tertiary"] {{
    min-height: 40px; font-size: 15px; font-weight: 500; color: #8B95A1;
    background: transparent; border: none; justify-content: center;
}}
.stButton button[kind="tertiary"]:hover, [data-testid="stBaseButton-tertiary"]:hover {{
    color: #4E5968;
}}
/* 비밀번호 입력칸 */
.stTextInput input {{
    background: #F2F4F6; border: none; border-radius: 14px;
    min-height: 52px; font-size: 17px; padding-left: 16px;
}}
.stTextInput [data-baseweb="input"], .stTextInput [data-baseweb="base-input"] {{
    background: #F2F4F6; border: none; border-radius: 14px;
}}
.stTextInput label p {{ font-size: 15px; color: #6B7684; }}
</style>
"""


def esc(x):
    return html.escape(str(x))


def text(cls, content):
    st.markdown(f'<div class="{cls}">{esc(content)}</div>', unsafe_allow_html=True)


def _button(label, wide=True, **kw):
    """Streamlit 버전에 따라 없는 옵션이 있으면 빼고 다시 시도"""
    tries = []
    if wide:
        tries.append(dict(kw, width="stretch"))
        tries.append(dict(kw, use_container_width=True))
    tries.append(kw)
    tries.append({k: v for k, v in kw.items() if k not in ("icon", "type")})
    for opts in tries:
        try:
            return st.button(label, **opts)
        except Exception:
            continue


def option_button(label, **kw):        # 선택지: 회색 버튼
    return _button(label, type="secondary", **kw)


def main_button(label, **kw):          # 꼭 누르는 버튼: 파란 버튼
    return _button(label, type="primary", **kw)


def nav_button(label, **kw):           # 이전 / 처음부터 다시: 회색 글자
    return _button(label, type="tertiary", **kw)


def nav_row(back=True, key=""):
    st.write("")
    if back:
        col1, col2 = st.columns(2)
        with col1:
            nav_button("← 이전", key=f"back{key}", on_click=go_back)
        with col2:
            nav_button("처음부터 다시", key=f"restart{key}", on_click=restart)
    else:
        nav_button("처음부터 다시", key=f"restart{key}", on_click=restart)


def restaurant_card(num, r, a):
    brk = r["브레이크타임"] or "없음"
    rows = [
        ("위치", r["위치"]),
        ("메뉴", r["대표메뉴"]),
        ("가격", f"{r['가격']:,}원"),
        ("영업", f"{r['영업시간']} (브레이크타임 {brk})"),
        ("휴무", r["휴무일"] or "없음"),
    ]
    body = "".join(
        f'<div class="row"><span class="label">{esc(k)}</span><span>{esc(v)}</span></div>'
        for k, v in rows
    )
    st.markdown(
        f'<div class="t-card"><div class="num">추천 {num}</div>'
        f'<div class="name">{esc(r["식당명"])}</div>{body}'
        f'<div class="why">추천 이유: {esc(reason_text(r, a))}</div></div>',
        unsafe_allow_html=True,
    )


def get_password():
    try:
        return st.secrets["password"]
    except Exception:
        return None


def page_login():
    text("t-title", APP_TITLE)
    password = get_password()
    if password is None:
        st.error("관리자 설정 필요: Streamlit Cloud의 Secrets에 password를 입력하세요.")
        return
    st.write("")
    with st.form("login", border=False):
        typed = st.text_input("비밀번호를 입력하세요.", type="password")
        ok = False
        for opts in (dict(width="stretch"), dict(use_container_width=True), {}):
            try:
                ok = st.form_submit_button("입장", type="primary", **opts)
                break
            except Exception:
                continue
    if ok:
        if typed == password:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("비밀번호가 맞지 않습니다.")


def page_start():
    text("t-title", APP_TITLE)
    text("t-sub", "몇 가지 질문에 답하면 부산대 근처 식당을 추천해 드립니다.")
    main_button("시작", on_click=start)


def page_question():
    step = st.session_state.step
    key, q, options = QUESTIONS[step]
    total = len(QUESTIONS)
    pct = int((step + 1) / total * 100)
    st.markdown(
        f'<div class="t-step">{step + 1} / {total}</div>'
        f'<div class="t-bar"><div style="width:{pct}%"></div></div>',
        unsafe_allow_html=True,
    )
    text("t-q", q)
    for opt in options:
        option_button(opt, key=f"q{step}_{opt}", on_click=choose, args=(key, opt))
    nav_row(back=True, key="_q")


def page_result():
    s = st.session_state
    a = s.answers

    if not s.queue:
        text("t-q", "추천 결과")
        text("t-sub", "조건에 맞는 식당이 없습니다. 조건을 바꿔 다시 시도해 주세요.")
        main_button("처음부터 다시", on_click=restart)
        return

    if s.stage == "end":
        text("t-q", "추천 결과")
        text("t-sub", "조건에 맞는 식당을 모두 보여드렸습니다.")
        main_button("처음부터 다시", on_click=restart)
        return

    if s.stage == "why":
        text("t-q", "어떤 점이 아쉬우신가요?")
        option_button("가격이 비싸요", on_click=retry, args=("price",))
        option_button("다른 종류를 원해요", on_click=retry, args=("kind",))
        option_button("그냥 싫어요", on_click=retry, args=("any",))
        nav_row(back=False, key="_w")
        return

    text("t-q", "추천 결과")
    for note in (
        "다른 식당을 추천합니다." if s.again else None,
        s.notice,
        "모든 조건에 맞는 곳이 없어 가장 비슷한 곳을 추천합니다." if s.partial else None,
    ):
        if note:
            text("t-note", note)

    for n, i in enumerate(s.shown, start=1):
        restaurant_card(n, s.queue[i], a)

    st.write("")
    main_button("다른 곳 볼래요", on_click=want_other)
    nav_row(back=False, key="_r")


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🍚", layout="centered")
    st.markdown(CSS, unsafe_allow_html=True)
    init_state()

    if not st.session_state.authed:
        page_login()
    elif st.session_state.page == "start":
        page_start()
    elif st.session_state.page == "question":
        page_question()
    else:
        page_result()


if __name__ == "__main__":
    main()
