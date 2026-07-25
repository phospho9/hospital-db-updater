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
# 2. 공공데이터 포털 API 설정 (병원기본목록)
# ---------------------------------------------------------------------------
api_url = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
num_of_rows = 500  # 안정적인 조회를 위해 페이지당 500건씩 수집
page_no = 1
total_collected = 0

print("🚀 스마트 병의원 기본 데이터(뼈대) 수집 및 D1 동기화를 시작합니다...")

# ---------------------------------------------------------------------------
# 3. 데이터 수집 및 가공 루프
# ---------------------------------------------------------------------------
while True:
    print(f"\n🔄 [{page_no}페이지] 수집 중... (페이지당 {num_of_rows}건)")
    
    params = {
        "serviceKey": DATA_GO_KR_API_KEY,
        "pageNo": str(page_no),
        "numOfRows": str(num_of_rows),
    }

    # API 호출 (최대 3회 재시도)
    api_res = None
    for attempt in range(1, 4):
        try:
            api_res = requests.get(api_url, params=params, timeout=60)
            if api_res.status_code == 200:
                break
        except requests.exceptions.RequestException:
            time.sleep(3)

    if not api_res or api_res.status_code != 200:
        print(f"❌ [{page_no}페이지] API 호출 실패로 수집을 마칩니다.")
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
            print("✅ 수집 완결!")
            break

        if isinstance(items, dict):
            items = [items]

        # SQL 문 생성
        sql_statements = []
        for item in items:
            hosp_id = str(item.get('ykiho', '')).replace("'", "''")
            name = str(item.get('yadmNm', '')).replace("'", "''")
            cl_name = str(item.get('clCdNm', '')).replace("'", "''")
            addr = str(item.get('addr', '')).replace("'", "''")
            phone = str(item.get('telno', '')).replace("'", "''")
            
            try:
                longitude = float(item.get('XPos', 0.0))
                latitude = float(item.get('YPos', 0.0))
            except (ValueError, TypeError):
                longitude, latitude = 0.0, 0.0

            # 병원 규모를 나타내는 '병상' 여부는 공공데이터 분류명으로 충분히 파악 가능
            has_beds = 1 if any(k in cl_name for k in ['병원', '요양병원']) else 0
            subjects = cl_name

            # ---------------------------------------------------------------------------
            # 💡 [핵심] 역할 분리: ON CONFLICT DO UPDATE 구문 수정
            # 새로 등록된 병원(INSERT)에는 기본값 0을 임시로 넣어줍니다. (네이버 크롤러가 나중에 채움)
            # 이미 등록된 병원(UPDATE)은 주소/전화번호 등 기본 뼈대만 업데이트하고,
            # 네이버 크롤러가 정성스럽게 모아둔 특화 진료(has_chuna 등) 값은 아예 건드리지 않습니다.
            # ---------------------------------------------------------------------------
            sql = f"""
            INSERT INTO hospitals (
                id, name, type, address, phone, latitude, longitude, 
                is_silbi, subjects, has_night, has_chuna, has_beds, 
                has_yakchim, is_cheopyak, updated_at
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
                updated_at=datetime('now');
            """
            sql_statements.append(sql)

        # ---------------------------------------------------------------------------
        # 5. Cloudflare D1으로 전송 실행
        # ---------------------------------------------------------------------------
        full_sql = " ".join(sql_statements)
        d1_res = requests.post(d1_url, headers=d1_headers, json={"sql": full_sql}, timeout=60)

        if d1_res.status_code == 200:
            total_collected += len(items)
            print(f"✅ [{page_no}페이지] {len(items)}건 DB 저장 완료 (누적: {total_collected} / 전체 {total_count}건)")
        else:
            print(f"❌ D1 전송 실패 (상태 코드 {d1_res.status_code}):", d1_res.text)
            break

        if total_collected >= total_count:
            print("🎉 전국의 모든 병의원 뼈대 데이터 동기화 완료!")
            break

        page_no += 1
        time.sleep(0.5)

    except Exception as e:
        print(f"❌ 예외 발생: {e}")
        break

print(f"\n✨ 수집 최종 완료! 총 수집건수: {total_collected}건")
