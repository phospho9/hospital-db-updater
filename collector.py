import os
import sys
import time
import requests
import xmltodict

# 환경변수 로드
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_DATABASE_ID = os.environ.get("CF_DATABASE_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "").strip()

if not all([CF_ACCOUNT_ID, CF_DATABASE_ID, CF_API_TOKEN, DATA_GO_KR_API_KEY]):
    print("❌ 에러: Secrets 정보(CF 정보 또는 DATA_GO_KR_API_KEY)가 부족합니다.")
    sys.exit(1)

# Cloudflare D1 API 설정
d1_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/raw"
d1_headers = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

# 공공데이터 포털 API 설정
api_url = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
num_of_rows = 1000  # 1회 요청당 최대 1,000건 수집
page_no = 1
total_collected = 0

print("🚀 전국 병의원 전체 데이터 대량 수집을 시작합니다...")

while True:
    print(f"\n🔄 [{page_no}페이지] 수집 요청 중... (페이지당 {num_of_rows}건)")
    
    params = {
        "serviceKey": DATA_GO_KR_API_KEY,
        "pageNo": str(page_no),
        "numOfRows": str(num_of_rows),
    }

    # API 재시도 로직 (최대 3회)
    api_res = None
    for attempt in range(1, 4):
        try:
            api_res = requests.get(api_url, params=params, timeout=60)
            if api_res.status_code == 200:
                break
        except requests.exceptions.RequestException as e:
            print(f"⚠️ 요청 지연/경고 ({e}), 5초 후 재시도... ({attempt}/3)")
            time.sleep(5)

    if not api_res or api_res.status_code != 200:
        print(f"❌ [{page_no}페이지] API 호출 실패로 수집을 종료합니다.")
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
            print("✅ 더 이상 가져올 데이터가 없습니다. 수집 완결!")
            break

        if isinstance(items, dict):
            items = [items]

        # SQL 문 생성 (INSERT OR UPDATE)
        sql_statements = []
        for item in items:
            hosp_id = str(item.get('ykiho', '')).replace("'", "''")         # 요양기호 (고유 ID)
            name = str(item.get('yadmNm', '')).replace("'", "''")          # 병원명
            cl_name = str(item.get('clCdNm', '')).replace("'", "''")       # 종별 (의원, 한의원, 병원 등)
            addr = str(item.get('addr', '')).replace("'", "''")            # 주소
            phone = str(item.get('telno', '')).replace("'", "''")          # 전화번호
            
            try:
                longitude = float(item.get('XPos', 0.0))
                latitude = float(item.get('YPos', 0.0))
            except (ValueError, TypeError):
                longitude, latitude = 0.0, 0.0

            sql = f"""
            INSERT INTO hospitals (id, name, type, address, phone, latitude, longitude, is_silbi, updated_at)
            VALUES ('{hosp_id}', '{name}', '{cl_name}', '{addr}', '{phone}', {latitude}, {longitude}, 0, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                type=excluded.type,
                address=excluded.address,
                phone=excluded.phone,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                updated_at=datetime('now');
            """
            sql_statements.append(sql)

        # Cloudflare D1으로 전송
        full_sql = " ".join(sql_statements)
        d1_res = requests.post(d1_url, headers=d1_headers, json={"sql": full_sql}, timeout=60)

        if d1_res.status_code == 200:
            total_collected += len(items)
            print(f"✅ [{page_no}페이지] {len(items)}건 D1 저장을 완료했습니다. (현재 누적: {total_collected} / 전체 약 {total_count}건)")
        else:
            print(f"❌ D1 전송 실패 (상태 코드 {d1_res.status_code}):", d1_res.text)
            break

        # 전체 개수에 도달했는지 체크
        if total_collected >= total_count:
            print("🎉 전국의 모든 병의원 데이터 수집 및 D1 저장 완료!")
            break

        page_no += 1
        time.sleep(1) # API 매너 대기시간 (1초)

    except Exception as e:
        print(f"❌ 처리 중 예외 발생: {e}")
        break

print(f"\n✨ 수집 작업 최종 완료! 총 수집건수: {total_collected}건")
