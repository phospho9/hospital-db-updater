import os
import sys
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

# 1. 공공데이터 포털 API 호출 (건강보험심사평가원 병원기본목록)
api_url = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
params = {
    "serviceKey": DATA_GO_KR_API_KEY,
    "pageNo": "1",
    "numOfRows": "100",  # 1회 호출 시 가져올 병의원 수
}

print("🔄 공공데이터 포털 API 요청 중...")
try:
    api_res = requests.get(api_url, params=params, timeout=15)
    if api_res.status_code != 200:
        print(f"❌ 공공데이터 API 호출 실패: 상태 코드 {api_res.status_code}")
        sys.exit(1)

    # XML 응답을 파이썬 딕셔너리로 변환
    data_dict = xmltodict.parse(api_res.text)
    header = data_dict.get('response', {}).get('header', {})
    
    if header.get('resultCode') != '00':
        print(f"❌ API 결과 에러: {header.get('resultMsg')}")
        sys.exit(1)

    items = data_dict.get('response', {}).get('body', {}).get('items', {}).get('item', [])
    if not items:
        print("⚠️ 수집된 데이터가 없습니다.")
        sys.exit(0)

    if isinstance(items, dict):  # 데이터가 1건만 반환된 경우 리스트로 변환
        items = [items]

    print(f"✅ 총 {len(items)}건의 실제 병의원 데이터를 수집했습니다.")

except Exception as e:
    print(f"❌ API 데이터 수집 중 에러 발생: {e}")
    sys.exit(1)

# 2. D1 데이터베이스 쿼리 생성
sql_statements = []
for item in items:
    hosp_id = str(item.get('ykiho', '')).replace("'", "''")         # 암호화된 요양기호 (고유 ID)
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

full_sql = " ".join(sql_statements)

# 3. Cloudflare D1 REST API로 데이터 전송
d1_url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/raw"
headers = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

try:
    print("🚀 Cloudflare D1 데이터베이스 업데이트 중...")
    d1_res = requests.post(d1_url, headers=headers, json={"sql": full_sql}, timeout=30)
    
    if d1_res.status_code == 200:
        print("🎉 실제 공공데이터 병의원 DB 업데이트 성공!")
    else:
        print(f"❌ D1 전송 실패 (상태 코드 {d1_res.status_code}):", d1_res.text)
        sys.exit(1)

except Exception as e:
    print("❌ D1 업로드 중 에러 발생:", e)
    sys.exit(1)
