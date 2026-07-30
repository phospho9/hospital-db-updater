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

# ---------------------------------------------------------------------------
# 2. 공공데이터 포털 API 설정 (롤링 수집 설정)
# ---------------------------------------------------------------------------
api_url = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
num_of_rows = 500  # 페이지당 500건
MAX_PAGES = 2      # 회당 2페이지 (총 1,000건) 수집 후 종료

print("🚀 공공데이터 롤링 동기화 수집 시작 (회당 1,000건)...")

# ---------------------------------------------------------------------------
# 3. 데이터 수집 및 D1 UPSERT
# ---------------------------------------------------------------------------
total_collected = 0

for page_no in range(1, MAX_PAGES + 1):
    print(f"\n🔄 [{page_no}/{MAX_PAGES}페이지] 공공데이터 API 조회 중... ({num_of_rows}건 요청)")
    
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
        print(f"❌ [{page_no}페이지] API 호출 실패로 이번 수집을 마칩니다.")
        break

    try:
        data_dict = xmltodict.parse(api_res.text)
        header = data_dict.get('response', {}).get('header', {})
        
        if header.get('resultCode') != '00':
            print(f"❌ API 에러 응답: {header.get('resultMsg')}")
            break

        body = data_dict.get('response', {}).get('body', {})
        items = body.get('items', {}).get('item', [])

        if not items:
            print("✅ 수집 대상 완결!")
            break

        if isinstance(items, dict):
            items = [items]

        sql_statements = []
        sample_hospitals = [] # 로그 출력용 샘플 병원 목록

        for idx, item in enumerate(items):
            hosp_id = str(item.get('ykiho', '')).replace("'", "''")
            name = str(item.get('yadmNm', '')).replace("'", "''")
            cl_name = str(item.get('clCdNm', '')).replace("'", "''")
            addr = str(item.get('addr', '')).replace("'", "''")
            phone = str(item.get('telno', '')).replace("'", "''")
            
            # 로그 출력을 위해 처음 3개 병원 샘플 추출
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
            print(f"✅ [{page_no}페이지] {len(items)}건 DB 동기화 성공! (이번 회차 누적: {total_collected}건)")
            print("   🏥 주요 갱신 병원 예시:")
            for sample in sample_hospitals:
                print(f"     {sample}")
            if len(items) > 3:
                print(f"     ... 외 {len(items) - 3}개 의료기관")
        else:
            print(f"❌ D1 전송 실패 (상태 코드 {d1_res.status_code}):", d1_res.text)
            break

        time.sleep(0.5)

    except Exception as e:
        print(f"❌ 예외 발생: {e}")
        break

print("\n" + "="*60)
print(f"✨ 이번 회차 동기화 완결! 총 {total_collected}개 병의원 기본 정보 최신화 완료")
print("="*60)
