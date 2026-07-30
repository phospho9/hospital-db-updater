import os
import sys
import time
import requests
import xmltodict

# ---------------------------------------------------------------------------
# 1. 환경변수 로드
# ---------------------------------------------------------------------------
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_DATABASE_ID = os.environ.get("CF_DATABASE_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "").strip()

if not all([CF_ACCOUNT_ID, CF_DATABASE_ID, CF_API_TOKEN, DATA_GO_KR_API_KEY]):
    print("❌ 에러: Secrets 정보가 부족합니다.")
    sys.exit(1)

# Cloudflare D1 API 설정
d1_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/raw"
d1_headers = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

def execute_d1(sql, params=[]):
    """D1 SQL 실행 전용 함수"""
    try:
        res = requests.post(d1_url, headers=d1_headers, json={"sql": sql, "params": params}, timeout=30)
        if res.status_code == 200:
            data = res.json()
            if data.get("success"):
                return data["result"][0].get("results", [])
            else:
                print(f"    ⚠️ D1 응답 에러: {data.get('errors')}")
        else:
            print(f"    ❌ D1 HTTP 에러 [{res.status_code}]: {res.text}")
    except Exception as e:
        print(f"    ❌ D1 실행 예외: {e}")
    return None

# ---------------------------------------------------------------------------
# 2. sync_state 테이블 자동 생성 및 지난 페이지 조회
# ---------------------------------------------------------------------------
# 테이블이 없으면 파이썬이 자동으로 생성합니다.
execute_d1("CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value INTEGER);")

state_res = execute_d1("SELECT value FROM sync_state WHERE key = 'last_page';")
last_page = 0
if state_res and len(state_res) > 0:
    last_page = state_res[0].get("value", 0)

start_page = last_page + 1
PAGES_PER_RUN = 2  # 회당 2페이지(1,000건)씩 진행
end_page = start_page + PAGES_PER_RUN - 1

api_url = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
num_of_rows = 500

print(f"🚀 공공데이터 롤링 동기화 시작! (이전 기록: {last_page}페이지 / 이번 회차: {start_page}~{end_page}페이지)")

# ---------------------------------------------------------------------------
# 3. 데이터 수집 및 이어서 저장
# ---------------------------------------------------------------------------
total_collected = 0
last_successful_page = last_page

for page_no in range(start_page, end_page + 1):
    print(f"\n🔄 [{page_no}페이지] 공공데이터 API 조회 중... ({num_of_rows}건 요청)")
    
    params = {
        "serviceKey": DATA_GO_KR_API_KEY,
        "pageNo": str(page_no),
        "numOfRows": str(num_of_rows),
    }

    api_res = None
    for attempt in range(1, 4):
        try:
            api_res = requests.get(api_url, params=params, timeout=60)
            if api_res.status_code == 200:
                break
        except requests.exceptions.RequestException:
            time.sleep(3)

    if not api_res or api_res.status_code != 200:
        print(f"❌ [{page_no}페이지] API 호출 실패로 이번 회차를 마칩니다.")
        break

    try:
        data_dict = xmltodict.parse(api_res.text)
        header = data_dict.get('response', {}).get('header', {})
        
        if header.get('resultCode') != '00':
            print(f"❌ API 에러 응답: {header.get('resultMsg')}")
            break

        body = data_dict.get('response', {}).get('body', {})
        total_count = int(body.get('totalCount', 0))
        items = body.get('items', {}).get('item', [])

        if not items:
            print("🎉 전국의 모든 병원 수집 완료! 다음 실행 시 1페이지부터 다시 시작합니다.")
            last_successful_page = 0  # 1페이지로 리셋
            break

        if isinstance(items, dict):
            items = [items]

        sql_statements = []
        sample_hospitals = []

        for idx, item in enumerate(items):
            hosp_id = str(item.get('ykiho', '')).replace("'", "''")
            name = str(item.get('yadmNm', '')).replace("'", "''")
            cl_name = str(item.get('clCdNm', '')).replace("'", "''")
            addr = str(item.get('addr', '')).replace("'", "''")
            phone = str(item.get('telno', '')).replace("'", "''")
            
            if idx < 3:
                short_addr = " ".join(addr.split()[:2]) if addr else "주소미상"
                sample_hospitals.append(f"• {name} ({cl_name} / {short_addr})")

            try:
                longitude = float(item.get('XPos', 0.0))
                latitude = float(item.get('YPos', 0.0))
            except (ValueError, TypeError):
                longitude, latitude = 0.0, 0.0

            has_beds = 1 if any(k in cl_name for k in ['병원', '요양병원']) else 0
            subjects = cl_name

            sql = f"""
            INSERT INTO hospitals (
                id, name, type, address, phone, latitude, longitude, 
                is_silbi, subjects, has_night, has_chuna, has_beds, 
                has_yakchim, is_cheopyak, info_updated_at
            )
            VALUES (
                '{hosp_id}', '{name}', '{cl_name}', '{addr}', '{phone}', {latitude}, {longitude}, 
                0, '{subjects}', 0, 0, {has_beds}, 
                0, 0, datetime('now')
            )
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                type=excluded.type,
                address=excluded.address,
                phone=excluded.phone,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                subjects=excluded.subjects,
                has_beds=excluded.has_beds,
                info_updated_at=datetime('now');
            """
            sql_statements.append(sql)

        full_sql = " ".join(sql_statements)
        d1_res = requests.post(d1_url, headers=d1_headers, json={"sql": full_sql}, timeout=60)

        if d1_res.status_code == 200:
            total_collected += len(items)
            last_successful_page = page_no
            print(f"✅ [{page_no}페이지] {len(items)}건 DB 동기화 성공! (이번 회차 누적: {total_collected}건)")
            print("   🏥 주요 갱신 병원 예시:")
            for sample in sample_hospitals:
                print(f"     {sample}")
            if len(items) > 3:
                print(f"     ... 외 {len(items) - 3}개 의료기관")
        else:
            print(f"❌ D1 전송 실패 (상태 코드 {d1_res.status_code}):", d1_res.text)
            break

        # 전체 건수 도달 시 다음엔 1페이지부터 리셋
        if page_no * num_of_rows >= total_count:
            print("\n🎉 전국 모든 병원 끝까지 수집 완료! 다음 실행 시 1페이지로 리셋됩니다.")
            last_successful_page = 0
            break

        time.sleep(0.5)

    except Exception as e:
        print(f"❌ 예외 발생: {e}")
        break

# ---------------------------------------------------------------------------
# 4. 진행된 마지막 페이지 번호 DB에 저장
# ---------------------------------------------------------------------------
update_state_sql = f"""
INSERT INTO sync_state (key, value) VALUES ('last_page', {last_successful_page})
ON CONFLICT(key) DO UPDATE SET value = {last_successful_page};
"""
execute_d1(update_state_sql)

print("\n" + "="*60)
print(f"✨ 동기화 완료! {start_page}~{last_successful_page}페이지 (총 {total_collected}개 병원 갱신)")
print(f"📌 다음 실행 위치: {last_successful_page + 1}페이지부터 진행 예정")
print("="*60)
