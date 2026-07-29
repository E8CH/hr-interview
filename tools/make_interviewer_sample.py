"""면접관명단 샘플(합성) 생성 — tools/fixtures/면접관명단_sample.xlsx

실제 면접관 명단에는 사번·이메일 같은 개인정보가 들어가므로 저장소에는
합성본만 둔다. 콘솔 3번 메뉴(면접 담당자 선별)와 04 단위 테스트가 쓴다.

    python tools/make_interviewer_sample.py
"""
from pathlib import Path

from openpyxl import Workbook

TEAMS = ["AI솔루션팀", "로봇응용기술팀", "미래혁신팀", "배터리기술팀", "전극기술팀"]
HEADER = ["사번", "성명", "직급", "소속팀", "이메일", "일일최대", "우선순위"]
TITLES = ["수석", "책임", "선임", "선임"]
OUT = Path(__file__).resolve().parent / "fixtures" / "면접관명단_sample.xlsx"


def build() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "면접관명단"
    sheet.append(HEADER)
    for team_no, team in enumerate(TEAMS, start=1):
        # 팀당 리더 1 + 실무 3 → 04 목 데이터와 같은 규모
        for member_no in range(4):
            is_leader = member_no == 0
            emp_id = f"IV{team_no}0{member_no + 1}"
            sheet.append([
                emp_id,
                f"{team} {'리더' if is_leader else f'실무{member_no}'}",
                TITLES[member_no % len(TITLES)],
                team,
                f"{emp_id.lower()}@example.com",
                4 if is_leader else 6,
                1 if is_leader else 2,
            ])
    return workbook


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().save(OUT)
    print(f"생성: {OUT}")
