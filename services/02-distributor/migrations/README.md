# migrations

PoC 단계에서는 Alembic 대신 애플리케이션 기동 시 `app.infrastructure.db.init_db()` 가
테이블 생성과 5개 팀 프로필 시딩을 수행한다 (멱등).

- 테이블: `team_profiles`, `distribution_plans`, `assignment_reasons`
- 시드: `app/domain/profile.py` 의 `TEAM_PROFILES`
- PostgreSQL의 `TEXT[]` 컬럼은 SQLite 호환을 위해 `JSON` 으로 매핑되어 있다.

PostgreSQL 전환 시에는 이 디렉토리에 Alembic 리비전을 추가하고
`init_db()` 의 `create_all` 호출을 제거한다.
